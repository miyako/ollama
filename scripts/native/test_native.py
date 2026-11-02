import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build
import detect
import publish
import smoke
import source


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((source.ROOT / "scripts/native/upstream.json").read_text())

    def upstream(self, path):
        if "/commits/" in path:
            return {"sha": self.lock["sha"]}
        return {"tag_name": self.lock["tag"], "draft": False, "prerelease": False,
                "published_at": self.lock["published_at"]}

    def test_pinned_and_latest_resolve_same_immutable_commit(self):
        with patch.object(source, "api", side_effect=self.upstream):
            pinned = source.resolve("pinned")
            latest = source.resolve("latest")
        self.assertEqual(pinned, latest)
        self.assertIn(self.lock["sha"][:12], pinned["release_tag"])

    def test_reject_branch_shell_and_prerelease_tags_before_api(self):
        with patch.object(source, "api") as api:
            for tag in ["main", "$(touch nope)", "v1.2.3-rc1", "../x", "v1.2.3\nx"]:
                with self.assertRaises(ValueError):
                    source.resolve("tag", tag)
            api.assert_not_called()

    def test_reject_moved_pin(self):
        with patch.object(source, "api", side_effect=self.upstream):
            with self.assertRaisesRegex(ValueError, "revision mismatch"):
                source.resolve("tag", self.lock["tag"], "0" * 40)

    def test_reject_draft_and_prerelease(self):
        for field in ["draft", "prerelease"]:
            release = self.upstream("")
            release[field] = True
            with patch.object(source, "api", return_value=release):
                with self.assertRaisesRegex(ValueError, "published stable"):
                    source.resolve("pinned")


class DetectTests(unittest.TestCase):
    def test_only_404_is_absent(self):
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "gh: Not Found (HTTP 404)")), \
                patch.object(subprocess, "check_output", return_value="[[]]"):
            self.assertIsNone(detect.existing_release("miyako/ollama", "native-test"))
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "gh: Bad credentials (HTTP 401)")):
            with self.assertRaises(RuntimeError):
                detect.existing_release("miyako/ollama", "native-test")

    def test_draft_missing_from_tag_endpoint_is_found_in_paginated_list(self):
        draft = {"id": 123, "tag_name": "native-test", "draft": True}
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "gh: Not Found (HTTP 404)")), \
                patch.object(subprocess, "check_output", return_value=json.dumps([[], [draft]])):
            self.assertEqual(detect.existing_release("miyako/ollama", "native-test"), draft)

    def test_draft_list_failure_is_not_treated_as_absent(self):
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "gh: Not Found (HTTP 404)")), \
                patch.object(subprocess, "check_output",
                             side_effect=subprocess.CalledProcessError(1, "gh")):
            with self.assertRaises(subprocess.CalledProcessError):
                detect.existing_release("miyako/ollama", "native-test")


class SmokeTests(unittest.TestCase):
    def test_shutdown_requires_zero_exit_after_graceful_signal(self):
        process = unittest.mock.Mock(returncode=0)
        process.poll.return_value = None
        smoke.stop_server(process)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=30)
        process.kill.assert_not_called()

    def test_hung_shutdown_is_cleaned_up_but_fails(self):
        process = unittest.mock.Mock(returncode=0)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("serve", 30), 0]
        with self.assertRaisesRegex(RuntimeError, "graceful signal shutdown"):
            smoke.stop_server(process)
        process.kill.assert_called_once()

    def test_mlx_requires_initialization_and_exact_missing_manifest_error(self):
        smoke.validate_mlx_result(subprocess.CompletedProcess(
            [], 1, "", 'msg="MLX engine initialized" device=gpu\nError: file does not exist\n'))
        for output in ["Error: file does not exist\n",
                       'msg="MLX engine initialized"\nError: failed to load library\n']:
            with self.assertRaisesRegex(RuntimeError, "MLX initialization smoke failed"):
                smoke.validate_mlx_result(subprocess.CompletedProcess([], 1, "", output))

    def test_only_new_listener_log_yields_address(self):
        self.assertIsNone(smoke.listening_address("OLLAMA_HOST=http://127.0.0.1:0"))
        self.assertIsNone(smoke.listening_address("Listening on 0.0.0.0:11434 (version 1.2.3)"))
        self.assertEqual(
            smoke.listening_address('time=now msg="Listening on 127.0.0.1:51728 (version 1.2.3)"'),
            ("127.0.0.1:51728", "1.2.3"))

    def test_environment_isolation(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:11434",
                                     "OLLAMA_MODELS": "/existing-models",
                                     "DYLD_LIBRARY_PATH": "/opt/homebrew/lib",
                                     "GGML_METAL_PATH_RESOURCES": "/old",
                                     "OLLAMA_LLAMACPP_PATH": "/old-runner"}):
            env = smoke.isolated_environment(Path("/isolated"))
        self.assertEqual(env["OLLAMA_HOST"], "127.0.0.1:0")
        self.assertEqual(env["OLLAMA_MODELS"], str(Path("/isolated/models")))
        self.assertNotIn("DYLD_LIBRARY_PATH", env)
        self.assertNotIn("OLLAMA_LLAMACPP_PATH", env)
        self.assertNotIn("GGML_METAL_PATH_RESOURCES", env)

    def test_early_exit_never_probes_another_server(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "ollama"
            binary.touch()
            with patch.object(smoke.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 1, "usage: --help")]), \
                    patch.object(smoke.subprocess, "Popen") as popen, \
                    patch.object(smoke.urllib.request, "build_opener") as opener:
                popen.return_value.poll.return_value = 1
                popen.return_value.returncode = 1
                with self.assertRaises(RuntimeError):
                    smoke.smoke(binary, "1.2.3", Path(temp) / "smoke.log")
                opener.assert_not_called()


class PackageTests(unittest.TestCase):
    def test_windows_gc_patch_is_idempotent_and_fails_closed(self):
        original = """int llama_server(int argc, char ** argv) {
    common_params params;

    common_init();

    // start the stream session manager GC right after common init, before any HTTP route can
    // touch it. lifecycle is symmetric, stop_gc() runs in clean_up() before backend free
    server_stream_session_manager_start();

    if (!common_params_parse(argc, argv, params, LLAMA_EXAMPLE_SERVER)) {
        return 1;
    }

    SRV_INF("%s", "initializing ...\\n");

    llama_backend_init();
}
"""
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp)
            subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
            code = checkout / "tools/server/server.cpp"
            code.parent.mkdir(parents=True)
            code.write_text(original)
            patches = build.patch_windows_helpers(checkout)
            changed = code.read_text()
            self.assertLess(changed.index("common_params_parse"), changed.index("server_stream_session_manager_start"))
            self.assertIn("windows-helper-gc.patch", patches)
            self.assertEqual(patches, build.patch_windows_helpers(checkout))
            self.assertEqual(code.read_text(), changed)
            code.write_text("unexpected source\n")
            with self.assertRaises(subprocess.CalledProcessError):
                build.patch_windows_helpers(checkout)

    def test_windows_toolchain_rejects_runner_fallback_and_mismatched_compiler(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp).resolve() / "ucrt64"
            licenses, compiler_bin = prefix / "share/licenses", prefix / "bin"
            licenses.mkdir(parents=True)
            compiler_bin.mkdir()
            (licenses / "COPYING.RUNTIME").touch()
            for name in ["gcc", "g++", "cmake", "ninja", "objdump"]:
                (compiler_bin / f"{name}.exe").touch()
            env = {"CC": str(compiler_bin / "gcc.exe"), "CXX": str(compiler_bin / "g++.exe")}
            with patch.dict(os.environ, env), \
                    patch.object(build.shutil, "which", side_effect=lambda name: str(compiler_bin / f"{name}.exe")), \
                    patch.object(build.subprocess, "check_output", return_value="x86_64-w64-mingw32\n"):
                self.assertEqual(build.windows_toolchain(licenses), compiler_bin)
                with patch.object(build.shutil, "which", return_value="C:/mingw64/bin/gcc.exe"):
                    with self.assertRaisesRegex(RuntimeError, "not a runner fallback"):
                        build.windows_toolchain(licenses)
                with patch.dict(os.environ, {"CC": "gcc"}):
                    with self.assertRaisesRegex(RuntimeError, "installed UCRT64 compiler"):
                        build.windows_toolchain(licenses)

    def test_windows_compiler_imports_are_bundled_recursively(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload, compiler, system = root / "payload", root / "compiler", root / "Windows"
            payload.mkdir()
            compiler.mkdir()
            (system / "System32").mkdir(parents=True)
            (payload / "ollama.exe").touch()
            (compiler / "libgcc_s_seh-1.dll").touch()
            (compiler / "libwinpthread-1.dll").touch()
            (system / "System32/KERNEL32.dll").touch()

            def imports(command, **_):
                name = Path(command[-1]).name
                header = "file format pei-x86-64\n"
                if name == "ollama.exe":
                    return header + "DLL Name: KERNEL32.dll\nDLL Name: libgcc_s_seh-1.dll\n"
                if name == "libgcc_s_seh-1.dll":
                    return header + "DLL Name: libwinpthread-1.dll\n"
                return header + "DLL Name: api-ms-win-crt-runtime-l1-1-0.dll\n"

            with patch.object(build.subprocess, "check_output", side_effect=imports):
                build.bundle_windows_imports(payload, compiler, system)
            self.assertTrue((payload / "libwinpthread-1.dll").is_file())
            with patch.object(build.subprocess, "check_output",
                              return_value="file format pei-x86-64\nDLL Name: missing.dll\n"):
                with self.assertRaisesRegex(RuntimeError, "Unpackaged Windows dependency"):
                    build.bundle_windows_imports(payload, compiler, system)

    def test_macos_rejects_build_machine_library_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ollama").touch()
            with patch.object(build, "run"), patch.object(
                    build.subprocess, "check_output",
                    side_effect=["Mach-O 64-bit executable arm64",
                                 "ollama:\n\t/opt/homebrew/lib/libomp.dylib (compatibility version 1.0)\n"]):
                with self.assertRaisesRegex(RuntimeError, "Unpackaged dependency"):
                    build.check_macos_libraries(root)

    def test_runtime_payload_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "llama-server"):
                build.require_payload(Path(temp), False)

    def test_zip_contains_entire_tree_and_executable_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp) / "package"
            runtime = tree / "macOS/arm64/lib/ollama"
            runtime.mkdir(parents=True)
            binary = tree / "macOS/arm64/ollama"
            binary.write_text("test")
            binary.chmod(0o755)
            (runtime / "LICENSE").write_text("license")
            archive = Path(temp) / "payload.zip"
            build.archive_tree(tree, archive)
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(package.read("macOS/arm64/lib/ollama/LICENSE"), b"license")
                self.assertTrue(package.getinfo("macOS/arm64/ollama").external_attr >> 16 & 0o111)

    def fixtures(self, downloads):
        source_info = {
            "tag": "v1.2.3", "sha": "a" * 40, "recipe": 1,
            "release_tag": "native-v1.2.3-aaaaaaaaaaaa-r1",
            "recipe_dirty": False,
            "recipe_sha": "b" * 40,
        }
        source_dir = downloads / "native-source"
        source_dir.mkdir()
        (source_dir / "source.json").write_text(json.dumps(source_info))
        for target in publish.TARGETS:
            folder = downloads / f"native-{target}"
            folder.mkdir()
            info = {**source_info, "target": target}
            (folder / f"{target}-BUILD-INFO.json").write_text(json.dumps(info))
            archive = folder / f"ollama-v1.2.3-aaaaaaaaaaaa-{target}-unsigned.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("payload/BUILD-INFO.json", json.dumps(info))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
        return source_info["release_tag"]

    def test_publisher_requires_complete_matching_checksums_and_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            tag = self.fixtures(downloads)
            _, assets = publish.verify_assets(downloads, tag)
            self.assertEqual(len(assets), 9)
            with self.assertRaisesRegex(ValueError, "tag does not match"):
                publish.verify_assets(downloads, "20.1.5")
            assets[0].write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                publish.verify_assets(downloads, tag)

    def test_verify_only_cli_needs_no_github_credentials_or_write_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            tag = self.fixtures(downloads)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("GH_", "GITHUB_"))}
            env["PATH"] = ""
            result = subprocess.run(
                [sys.executable, str(Path(publish.__file__).resolve()),
                 "--verify-only", str(downloads)], env=env, text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["release_tag"], tag)
            self.assertEqual(len(json.loads(result.stdout)["assets"]), 9)

    def test_verify_only_cli_refuses_uncommitted_recipes(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            self.fixtures(downloads)
            source_file = downloads / "native-source/source.json"
            source_info = json.loads(source_file.read_text())
            source_info["recipe_dirty"] = True
            source_file.write_text(json.dumps(source_info))
            result = subprocess.run(
                [sys.executable, str(Path(publish.__file__).resolve()),
                 "--verify-only", str(downloads)], text=True, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)

    def test_publisher_rejects_mixed_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            tag = self.fixtures(downloads)
            info = downloads / "native-macos-arm64/macos-arm64-BUILD-INFO.json"
            changed = json.loads(info.read_text())
            changed["sha"] = "b" * 40
            info.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "Source mismatch"):
                publish.verify_assets(downloads, tag)

    def test_publication_is_manual_main_only_and_never_overwrites(self):
        env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "r" * 40,
               "RELEASE_TAG": "native-v1.2.3-aaaaaaaaaaaa-r1"}
        info = {"recipe_sha": env["GITHUB_SHA"], "recipe_dirty": False}
        with patch.dict(os.environ, {**env, "GITHUB_EVENT_NAME": "schedule"}), \
                patch.object(publish, "verify_assets") as verify:
            with self.assertRaisesRegex(RuntimeError, "explicit dispatches"):
                publish.main()
            verify.assert_not_called()
        with patch.dict(os.environ, env), \
                patch.object(publish, "verify_assets", return_value=(info, [])), \
                patch.object(publish, "existing_release", return_value={"draft": False}), \
                patch.object(publish.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
                publish.main()
            run.assert_not_called()

    def test_draft_with_missing_assets_is_not_published(self):
        env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "r" * 40,
               "RELEASE_TAG": "native-v1.2.3-aaaaaaaaaaaa-r1"}
        info = {"recipe_sha": env["GITHUB_SHA"], "recipe_dirty": False,
                "tag": "v1.2.3", "sha": "a" * 40, "recipe": 1}
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, env), \
                    patch.object(publish, "verify_assets", return_value=(info, [Path("one.zip")])), \
                    patch.object(publish, "existing_release",
                                 return_value=None), \
                    patch.object(publish, "api", side_effect=[
                        {"id": 123},
                        {"draft": True, "prerelease": False, "tag_name": env["RELEASE_TAG"],
                         "target_commitish": info["recipe_sha"], "assets": []}]) as api, \
                    patch.object(publish.subprocess, "run",
                                 return_value=subprocess.CompletedProcess([], 1, "", "HTTP 404")) as run:
                with self.assertRaisesRegex(RuntimeError, "draft left unpublished"):
                    publish.main()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(len(commands), 2)
                self.assertNotIn("PATCH", str(api.call_args_list))

    def release_fixture(self, tag, info, assets):
        return {
            "id": 123, "tag_name": tag, "target_commitish": info["recipe_sha"],
            "draft": True, "prerelease": False,
            "assets": [{"name": path.name, "size": path.stat().st_size, "state": "uploaded",
                        "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
                       for path in assets],
        }

    def test_draft_verification_refuses_unexpected_state_or_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            tag = self.fixtures(downloads)
            info, assets = publish.verify_assets(downloads, tag)
            release = self.release_fixture(tag, info, assets)
            publish.verify_draft(release, tag, info, assets)
            for change in [{"draft": False}, {"prerelease": True}, {"tag_name": "wrong"},
                           {"target_commitish": "c" * 40}, {"assets": release["assets"][:-1]}]:
                with self.assertRaisesRegex(RuntimeError, "draft left unpublished"):
                    publish.verify_draft({**release, **change}, tag, info, assets)
            for change in [{"digest": "sha256:" + "0" * 64}, {"size": 0}, {"state": "new"}]:
                changed = {**release, "assets": [{**release["assets"][0], **change},
                                                *release["assets"][1:]]}
                with self.assertRaisesRegex(RuntimeError, "digest/size mismatch"):
                    publish.verify_draft(changed, tag, info, assets)

    def original_run(self, info):
        run = {"event": "workflow_dispatch", "head_branch": "main",
               "head_repository": {"full_name": "miyako/ollama"},
               "path": ".github/workflows/native-build.yml", "status": "completed",
               "conclusion": "failure", "head_sha": info["recipe_sha"]}
        jobs = {"jobs": [{"name": name, "conclusion": "success"} for name in [
            "source", "Verify complete release artifact contract (no publication)",
            "build (macos-15, macos-arm64)", "build (macos-15-intel, macos-x86_64)",
            "build (windows-2022, windows-x86_64)"]]
            + [{"name": "publish / publish", "conclusion": "failure"}]}
        return run, [jobs]

    def test_resume_refuses_unverified_run_or_failed_native_build(self):
        info = {"recipe_sha": "b" * 40}
        run, pages = self.original_run(info)
        for change in [{"event": "pull_request"}, {"head_branch": "other"},
                       {"head_sha": "c" * 40}, {"status": "in_progress"},
                       {"conclusion": "success"},
                       {"path": ".github/workflows/other.yml"},
                       {"head_repository": {"full_name": "attacker/fork"}}]:
            with patch.object(publish, "api", return_value={**run, **change}):
                with self.assertRaisesRegex(ValueError, "original failed main"):
                    publish.verify_build_run("miyako/ollama", "42", info)
        pages[0]["jobs"][2]["conclusion"] = "failure"
        with patch.object(publish, "api", side_effect=[run, pages]):
            with self.assertRaisesRegex(ValueError, "all original builds"):
                publish.verify_build_run("miyako/ollama", "42", info)

    def test_resume_uses_original_recipe_and_never_uploads_or_recreates_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            tag = self.fixtures(downloads)
            info, assets = publish.verify_assets(downloads, tag)
            draft = self.release_fixture(tag, info, assets)
            run, jobs = self.original_run(info)
            env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "c" * 40,
                   "RELEASE_TAG": tag, "SOURCE_RUN_ID": "42", "RESUME_DRAFT_ID": "123"}
            with patch.dict(os.environ, env), \
                    patch.object(publish, "verify_assets", return_value=(info, assets)), \
                    patch.object(publish, "api", side_effect=[
                        run, jobs, draft,
                        {**draft, "draft": False, "html_url": "https://github.com/release"}]) as api, \
                    patch.object(publish.subprocess, "run",
                                 return_value=subprocess.CompletedProcess([], 1, "", "HTTP 404")) as command:
                publish.main()
                self.assertEqual(command.call_count, 1)
                self.assertEqual(api.call_args.args,
                                 ("miyako/ollama", "releases/123", "--method", "PATCH",
                                  "-F", "draft=false", "-f", "make_latest=false"))
                self.assertNotIn("POST", str(api.call_args_list))

    def test_new_publication_uses_returned_release_id_not_tag_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)
            tag = self.fixtures(downloads)
            info, assets = publish.verify_assets(downloads, tag)
            draft = self.release_fixture(tag, info, assets)
            env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": info["recipe_sha"],
                   "RELEASE_TAG": tag, "SOURCE_RUN_ID": "", "RESUME_DRAFT_ID": ""}
            with patch.dict(os.environ, env), \
                    patch.object(publish, "verify_assets", return_value=(info, assets)), \
                    patch.object(publish, "existing_release", return_value=None) as lookup, \
                    patch.object(publish, "api", side_effect=[
                        {"id": 123}, draft,
                        {**draft, "draft": False, "html_url": "https://github.com/release"}]) as api, \
                    patch.object(publish.subprocess, "run",
                                 return_value=subprocess.CompletedProcess([], 1, "", "HTTP 404")):
                publish.main()
                lookup.assert_called_once()
                self.assertEqual(api.call_args_list[1].args,
                                 ("miyako/ollama", "releases/123"))
                self.assertIn("PATCH", api.call_args.args)


if __name__ == "__main__":
    unittest.main()
