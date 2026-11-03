import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build
import component
import detect
import install
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
    def test_only_trusted_main_events_reuse_an_existing_published_release(self):
        for event, ref, expected in [("push", "refs/heads/main", False),
                                     ("schedule", "refs/heads/main", False),
                                     ("workflow_dispatch", "refs/heads/main", False),
                                     ("pull_request", "refs/pull/3/merge", True)]:
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "output"
                env = {"EVENT": event, "GITHUB_REF": ref, "GITHUB_REPOSITORY": "miyako/ollama",
                       "GITHUB_OUTPUT": str(output)}
                with patch.dict(os.environ, env), patch.object(detect, "Path") as path, \
                        patch.object(detect, "existing_release",
                                     return_value={"draft": False, "prerelease": False}) as lookup:
                    path.return_value.read_text.return_value = json.dumps({"release_tag": "native-existing"})
                    detect.main()
                    self.assertEqual(output.read_text(), f"build={str(expected).lower()}\n")
                    self.assertEqual(lookup.call_count, 0 if expected else 1)

    def test_existing_draft_blocks_automatic_rebuild_and_overwrite(self):
        env = {"EVENT": "push", "GITHUB_REF": "refs/heads/main",
               "GITHUB_REPOSITORY": "miyako/ollama"}
        with patch.dict(os.environ, env), patch.object(detect, "Path") as path, \
                patch.object(detect, "existing_release",
                             return_value={"draft": True, "prerelease": False}):
            path.return_value.read_text.return_value = json.dumps({"release_tag": "native-existing"})
            with self.assertRaisesRegex(RuntimeError, "inspect/recover"):
                detect.main()

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

    def test_publication_is_trusted_main_only_and_never_overwrites(self):
        env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": "r" * 40,
               "RELEASE_TAG": "native-v1.2.3-aaaaaaaaaaaa-r1"}
        info = {"recipe_sha": env["GITHUB_SHA"], "recipe_dirty": False}
        with patch.dict(os.environ, {**env, "GITHUB_EVENT_NAME": "pull_request"}), \
                patch.object(publish, "verify_assets") as verify:
            with self.assertRaisesRegex(RuntimeError, "trusted main"):
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
                with self.assertRaisesRegex(RuntimeError, "asset set mismatch"):
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
                with self.assertRaisesRegex(RuntimeError, "draft left unpublished|asset set mismatch"):
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
            for event in ("push", "schedule", "workflow_dispatch"):
                with patch.dict(os.environ, {**env, "GITHUB_EVENT_NAME": event}), \
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


class ComponentIntegrationTests(unittest.TestCase):
    def apple_double_metadata(self):
        return (struct.pack(">II16sH", 0x00051607, 0x00020000, b"Mac OS X        ", 2)
                + struct.pack(">III", 9, 50, 32)
                + struct.pack(">III", 2, 82, 0)
                + bytes(32))

    def fixtures(self, downloads, source_info=None):
        if source_info is None:
            source_info = {**install.locked_source(), "recipe_sha": "b" * 40, "recipe_dirty": False}
        (downloads / "native-source").mkdir(parents=True)
        (downloads / "native-source/source.json").write_text(json.dumps(source_info))
        for target, (layout, binary) in install.LAYOUTS.items():
            folder = downloads / f"native-{target}"
            folder.mkdir()
            info = {**source_info, "target": target}
            (folder / f"{target}-BUILD-INFO.json").write_text(json.dumps(info))
            files = {binary: b"binary", "LICENSE": b"license",
                     "BUILD-INFO.json": json.dumps(info).encode()}
            if target.startswith("macos"):
                files[binary] = bytes.fromhex("cffaedfe") + b"test Mach-O"
            suffix = ".exe" if target.startswith("windows") else ""
            for name in ("llama-server" + suffix, "llama-quantize" + suffix,
                         "GO_LICENSE", "LLAMA_CPP_LICENSE", "LLAMA_CPP_VENDORS_LICENSE"):
                files["lib/ollama/" + name] = b"runtime"
            if target == "macos-arm64":
                for name in ("MLX_LICENSE", "MLX_C_LICENSE", "XGRAMMAR_LICENSE", "XGRAMMAR_NOTICE"):
                    files["lib/ollama/" + name] = b"license"
                for name in ("libmlx.dylib", "libmlxc.dylib", "libollama_xgrammar.dylib", "mlx.metallib"):
                    files["lib/ollama/mlx_metal_v3/" + name] = b"MLX runtime"
            if target.startswith("windows"):
                files["compiler-licenses/gcc/COPYING.RUNTIME"] = b"runtime exception"
                files["lib/ollama/ggml.dll"] = b"DLL"
            archive = folder / f"ollama-{source_info['tag']}-{source_info['sha'][:12]}-{target}-unsigned.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for name, content in files.items():
                    entry = zipfile.ZipInfo(layout + "/" + name)
                    entry.external_attr = (0o100755 if name == binary else 0o100644) << 16
                    package.writestr(entry, content)
            archive.with_suffix(".zip.sha256").write_text(
                f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
        return source_info

    def newer_source(self):
        locked = install.locked_source()
        major, minor, patch_version = map(int, locked["version"].split("."))
        version = f"{major}.{minor}.{patch_version + 1}"
        sha = "c" * 40
        return {**locked, "tag": "v" + version, "version": version, "sha": sha,
                "release_tag": f"native-v{version}-{sha[:12]}-r{locked['recipe']}",
                "recipe_sha": "b" * 40, "recipe_dirty": False}

    def test_cli_prepare_and_install_share_newer_resolved_source_without_adopting_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifacts, prepared = root / "artifacts", root / "prepared"
            info = self.fixtures(artifacts, self.newer_source())
            selected = artifacts / "native-source/source.json"
            destination = root / "ollama.4dbase/Resources/bin"
            command = [sys.executable, str(source.ROOT / "scripts/native/install.py")]
            subprocess.run([*command, "prepare", "--artifacts", str(artifacts),
                            "--source", str(selected), "--destination", str(prepared)],
                           check=True, capture_output=True, text=True)
            rejected = subprocess.run([*command, "install", str(prepared), str(destination)],
                                      capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("does not match the selected source/recipe", rejected.stderr)
            self.assertFalse(destination.exists())
            subprocess.run([*command, "install", str(prepared), str(destination),
                            "--source", str(selected)], check=True, capture_output=True, text=True)
            subprocess.run([*command, "verify", str(destination)],
                           check=True, capture_output=True, text=True)
            self.assertEqual(install.verify_installed(destination)["source"], info)
            self.assertNotEqual(install.locked_source()["sha"], info["sha"])

    def test_cli_prepare_and_install_default_to_pinned_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            prepared, destination = root / "prepared", root / "installed"
            with patch.object(sys, "argv", ["install.py", "prepare", "--artifacts",
                                           str(root / "artifacts"), "--destination", str(prepared)]):
                install.main()
            with patch.object(sys, "argv", ["install.py", "install", str(prepared), str(destination)]):
                install.main()
            self.assertEqual(install.verify_installed(destination)["source"], info)

    def test_cli_install_rejects_wrong_explicit_selection_before_replacing_resources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts", self.newer_source())
            prepared, destination = root / "prepared", root / "installed"
            install.prepare(root / "artifacts", prepared, info)
            destination.mkdir()
            sentinel = destination / "untouched"
            sentinel.write_text("existing resources")
            selected = root / "selected.json"
            for key, value in [("tag", "v0.0.1"), ("sha", "d" * 40),
                               ("recipe", info["recipe"] + 1),
                               ("release_tag", install.locked_source()["release_tag"])]:
                with self.subTest(key=key):
                    selected.write_text(json.dumps({**info, key: value}))
                    with patch.object(sys, "argv", ["install.py", "install", str(prepared),
                                                   str(destination), "--source", str(selected)]):
                        with self.assertRaisesRegex(ValueError, "selected source/recipe"):
                            install.main()
                    self.assertEqual(list(destination.iterdir()), [sentinel])
                    self.assertEqual(sentinel.read_text(), "existing resources")

    def test_complete_native_payload_replaces_legacy_component_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_info = self.fixtures(root / "artifacts")
            prepared = root / "prepared"
            manifest = install.prepare(root / "artifacts", prepared, source_info)
            destination = root / "ollama.4dbase/Resources/bin"
            (destination / "macOS").mkdir(parents=True)
            (destination / "macOS/ollama-x86_64").write_text("legacy")
            install.install(prepared, destination)
            self.assertFalse((destination / "macOS/ollama-x86_64").exists())
            self.assertEqual(install.verify_installed(destination), manifest)
            self.assertTrue((destination / "macOS/arm64/ollama").stat().st_mode & 0o111)
            self.assertTrue((destination / "macOS/x86_64/ollama-x86_64").is_file())
            self.assertTrue((destination / "Windows/lib/ollama/ggml.dll").is_file())
            self.assertTrue((destination / "macOS/arm64/lib/ollama/mlx_metal_v3/mlx.metallib").is_file())

    def test_staging_rejects_source_mismatch_and_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            with self.assertRaisesRegex(ValueError, "selected source"):
                install.prepare(root / "artifacts", root / "wrong", {**info, "sha": "c" * 40})
            archive = next((root / "artifacts/native-macos-arm64").glob("*.zip"))
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr("macOS/arm64/../../../escape", "bad")
            archive.with_suffix(".zip.sha256").write_text(f"{install.digest(archive)}  {archive.name}\n")
            with self.assertRaisesRegex(ValueError, "Unsafe/unexpected"):
                install.prepare(root / "artifacts", root / "unsafe", info)
            self.assertFalse((root / "escape").exists())

    def test_component_resources_reject_missing_extra_and_corrupt_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            prepared = root / "prepared"
            install.prepare(root / "artifacts", prepared, info)
            extra = prepared / "macOS/ollama-x86_64"
            extra.write_text("legacy")
            with self.assertRaisesRegex(ValueError, "missing/extra"):
                install.verify_installed(prepared)
            extra.unlink()
            library = prepared / "Windows/lib/ollama/ggml.dll"
            library.write_text("corrupt")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                install.verify_installed(prepared, signed=True)
            library.unlink()
            with self.assertRaisesRegex(ValueError, "missing/extra"):
                install.verify_installed(prepared)

    def test_signed_component_checks_both_mac_architectures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            prepared = root / "prepared"
            install.prepare(root / "artifacts", prepared, info)
            with patch.object(install.subprocess, "run") as command:
                install.verify_installed(prepared, signed=True)
                arguments = [call.args[0] for call in command.call_args_list]
                self.assertEqual(sum(args[0] == "codesign" for args in arguments), 2)
                self.assertEqual({args[-1] for args in arguments if args[0] == "lipo"},
                                 {"arm64", "x86_64"})
            with patch.object(install.subprocess, "run",
                              side_effect=subprocess.CalledProcessError(1, "codesign")):
                with self.assertRaises(subprocess.CalledProcessError):
                    install.verify_installed(prepared, signed=True)

    def test_actual_component_zip_has_verified_native_resource_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            prepared = root / "prepared"
            manifest = install.prepare(root / "artifacts", prepared, info)
            archive = root / "ollama.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for path in prepared.rglob("*"):
                    if path.is_file():
                        package.write(path, "ollama.4dbase/Resources/bin/" + path.relative_to(prepared).as_posix())
            component.verify_archive(archive, manifest, signed=False)
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr("ollama.4dbase/Resources/bin/macOS/ollama-x86_64", "legacy")
            with self.assertRaisesRegex(ValueError, "missing/extra"):
                component.verify_archive(archive, manifest, signed=False)

    def test_signed_json_appledouble_sidecars_are_metadata_not_extra_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            prepared = root / "prepared"
            manifest = install.prepare(root / "artifacts", prepared, info)
            archive = root / "ollama.zip"
            with zipfile.ZipFile(archive, "w") as package:
                prefix = "ollama.4dbase/Resources/bin/"
                for path in prepared.rglob("*"):
                    if path.is_file():
                        package.write(path, prefix + path.relative_to(prepared).as_posix())
                for name in ("._native-source.json", "macOS/arm64/._BUILD-INFO.json", "macOS/._arm64"):
                    package.writestr(prefix + name, self.apple_double_metadata())
            component.verify_archive(archive, manifest, signed=False)
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr(prefix + "._unexpected-binary", self.apple_double_metadata())
            with self.assertRaisesRegex(ValueError, "Unexpected component metadata sidecar"):
                component.verify_archive(archive, manifest, signed=False)

    def test_appledouble_recognition_rejects_malformed_and_data_fork_entries(self):
        name = PurePosixPath("._native-source.json")
        allowed = {"native-source.json"}
        valid = self.apple_double_metadata()
        self.assertTrue(component.apple_double(name, valid, allowed))
        invalid = [
            b"not metadata", b"BAD!" + valid[4:],
            valid[:24] + struct.pack(">H", 3) + valid[26:],
            valid[:26] + struct.pack(">III", 1, 50, 32) + valid[38:],
            valid[:26] + struct.pack(">III", 9, 1000, 32) + valid[38:],
            valid[:38] + struct.pack(">III", 2, 60, 10) + valid[50:],
        ]
        for data in invalid:
            with self.assertRaises(ValueError):
                component.apple_double(name, data, allowed)

    def test_native_download_requires_published_complete_release_before_network_download(self):
        expected = install.locked_source()
        for release in (None, {"draft": True}, {"draft": False, "prerelease": True}):
            with patch.object(install, "existing_release", return_value=release), \
                    patch.object(install.subprocess, "run") as command:
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    install.download_release(expected, Path("must-not-create"))
                command.assert_not_called()
        with patch.object(install, "existing_release",
                          return_value={"draft": False, "prerelease": False, "assets": []}), \
                patch.object(install.subprocess, "run") as command:
            with self.assertRaisesRegex(ValueError, "incomplete/unexpected"):
                install.download_release(expected, Path("must-not-create"))
            command.assert_not_called()

    def test_identical_component_skips_dispatch_and_version_churn(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "native-source.json"
            manifest.write_text(json.dumps({"source": {"release_tag": "native-existing"}}))
            env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME": "push"}
            with patch.dict(os.environ, env), \
                    patch.object(component, "component_matches", return_value=True), \
                    patch.object(component.subprocess, "run") as command, \
                    patch.object(component.subprocess, "check_output") as git:
                component.dispatch(manifest)
                command.assert_not_called()
                git.assert_not_called()

    def test_component_dispatch_requires_unchanged_main_and_exact_native_tag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.fixtures(root / "artifacts")
            manifest = root / "native-source.json"
            manifest.write_text(json.dumps({"source": info}))
            env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "b" * 40}
            with patch.dict(os.environ, env), \
                    patch.object(component, "component_matches", return_value=False), \
                    patch.object(component, "api", return_value={"object": {"sha": "b" * 40}}), \
                    patch.object(component.subprocess, "check_output", return_value="b" * 40 + "\n"), \
                    patch.object(component.subprocess, "run") as command:
                component.dispatch(manifest)
                self.assertEqual(command.call_count, 1)
                args = command.call_args.args[0]
                self.assertIn("native_release_tag=" + info["release_tag"], args)
                self.assertIn("source_sha=" + "b" * 40, args)
                self.assertNotIn("--force", args)
                with patch.object(component, "api", return_value={"object": {"sha": "c" * 40}}):
                    with self.assertRaisesRegex(RuntimeError, "main changed"):
                        component.dispatch(manifest)

    def test_component_adopts_newer_source_lock_before_dispatching_pinned_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            info = self.newer_source()
            lock_path = root / "scripts/native/upstream.json"
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text((source.ROOT / "scripts/native/upstream.json").read_text())
            manifest = root / "native-source.json"
            manifest.write_text(json.dumps({"source": info}))
            env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME": "schedule", "GITHUB_SHA": "b" * 40}
            with patch.dict(os.environ, env), patch.object(component, "ROOT", root), \
                    patch.object(component, "component_matches", return_value=False), \
                    patch.object(component, "api", return_value={"object": {"sha": "b" * 40}}), \
                    patch.object(component.subprocess, "check_output",
                                 side_effect=["b" * 40 + "\n", "d" * 40 + "\n"]), \
                    patch.object(component.subprocess, "run") as command:
                component.dispatch(manifest)
            self.assertEqual(json.loads(lock_path.read_text()),
                             {key: info[key] for key in ("repository", "tag", "sha", "published_at", "recipe")})
            commands = [call.args[0] for call in command.call_args_list]
            self.assertEqual(commands[-2], ["git", "push", "origin", "HEAD:refs/heads/main"])
            self.assertEqual(commands[-1][:4], ["gh", "workflow", "run", "publish.yml"])
            self.assertIn("source_sha=" + "d" * 40, commands[-1])
            self.assertIn("native_release_tag=" + info["release_tag"], commands[-1])

    def test_latest_legacy_component_needs_native_adoption_but_partial_new_release_fails(self):
        with patch.object(component, "api", return_value={"tag_name": "20.1.5", "assets": []}):
            self.assertFalse(component.component_matches({}))
        with patch.object(component, "api", return_value={
                "tag_name": "20.1.6", "assets": [{"name": "native-source.json", "state": "uploaded"}]}):
            with self.assertRaisesRegex(ValueError, "incomplete/invalid"):
                component.component_matches({})

    def test_component_idempotence_checks_actual_provenance_and_archive_checksum_contract(self):
        manifest = {"source": {"release_tag": "native-existing"}, "files": {}, "assets": {}}
        data = {"ollama.zip": b"ZIP", "ollama.dmg": b"DMG",
                "native-source.json": json.dumps(manifest).encode()}
        data["SHA256SUMS"] = "".join(
            f"{hashlib.sha256(data[name]).hexdigest()}  {name}\n"
            for name in ("ollama.dmg", "ollama.zip", "native-source.json")).encode()

        def metadata():
            return {"tag_name": "20.1.6", "assets": [
                {"name": name, "state": "uploaded", "size": len(value),
                 "digest": "sha256:" + hashlib.sha256(value).hexdigest()}
                for name, value in data.items()]}

        def download(command, **_):
            folder = Path(command[command.index("--dir") + 1])
            for name in ("native-source.json", "SHA256SUMS"):
                (folder / name).write_bytes(data[name])

        with patch.object(component, "api", side_effect=lambda *_: metadata()), \
                patch.object(component.subprocess, "run", side_effect=download):
            self.assertTrue(component.component_matches(manifest))
            self.assertFalse(component.component_matches({**manifest, "files": {"new": {}}}))
            data["SHA256SUMS"] = b"bad archive checksum contract\n"
            with self.assertRaisesRegex(ValueError, "archive checksum contract"):
                component.component_matches(manifest)

    def test_failed_component_build_reuses_unpublished_version_without_another_bump(self):
        env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch"}
        cases = [
            ("20.1.5", {"draft": False, "prerelease": False}, "true"),
            ("20.1.6", None, "false"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for current, release, expected in cases:
                (root / "package.json").write_text(json.dumps({"version": current}))
                output = root / "output"
                output.write_text("")
                with patch.dict(os.environ, {**env, "GITHUB_OUTPUT": str(output)}), \
                        patch.object(component, "ROOT", root), \
                        patch.object(component, "existing_release", return_value=release), \
                        patch.object(component, "api", return_value={"tag_name": "20.1.5"}), \
                        patch.object(component.subprocess, "run") as command:
                    component.select_version()
                    self.assertIn(f"bump={expected}\n", output.read_text())
                    command.assert_not_called()
            with patch.dict(os.environ, env), patch.object(component, "ROOT", root), \
                    patch.object(component, "existing_release",
                                 return_value={"draft": True, "prerelease": False}):
                with self.assertRaisesRegex(RuntimeError, "requires inspection"):
                    component.select_version()

    def test_component_publication_is_immutable_complete_and_latest_only_after_id_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".build").mkdir()
            info = self.fixtures(root / "artifacts")
            prepared = root / "prepared"
            install.prepare(root / "artifacts", prepared, info)
            dmg, archive, manifest = root / "ollama.dmg", root / "ollama.zip", prepared / "native-source.json"
            dmg.write_bytes(b"DMG")
            archive.write_bytes(b"ZIP")
            head = "c" * 40

            def release_api(repository, path, *arguments):
                if path == "releases":
                    self.assertIn("draft=true", arguments)
                    self.assertIn("make_latest=false", arguments)
                    return {"id": 123}
                self.assertEqual(path, "releases/123")
                if "PATCH" in arguments:
                    self.assertIn("make_latest=true", arguments)
                    return {"html_url": "https://github.com/component", "draft": False,
                            "tag_name": "20.1.6", "target_commitish": head}
                files = [dmg, archive, manifest, root / ".build/SHA256SUMS"]
                return {"draft": True, "prerelease": False, "tag_name": "20.1.6",
                        "target_commitish": head, "assets": [
                            {"name": file.name, "size": file.stat().st_size, "state": "uploaded",
                             "digest": "sha256:" + install.digest(file)} for file in files]}

            env = {"GITHUB_REPOSITORY": "miyako/ollama", "GITHUB_REF": "refs/heads/main",
                   "GITHUB_EVENT_NAME": "workflow_dispatch"}
            with patch.dict(os.environ, env), patch.object(component, "ROOT", root), \
                    patch.object(component, "verify_archive") as verify, \
                    patch.object(component, "existing_release", return_value=None), \
                    patch.object(component.subprocess, "check_output", return_value=head + "\n"), \
                    patch.object(component.subprocess, "run",
                                 return_value=subprocess.CompletedProcess([], 1, "", "HTTP 404")) as command, \
                    patch.object(component, "api", side_effect=release_api) as api:
                component.publish_component("20.1.6", dmg, archive, manifest)
                verify.assert_called_once()
                self.assertEqual(command.call_count, 2)
                self.assertNotIn("--clobber", str(command.call_args_list))
                self.assertIn("PATCH", api.call_args.args)
                with patch.object(component, "existing_release", return_value={"draft": False}):
                    with self.assertRaisesRegex(RuntimeError, "Refusing to overwrite"):
                        component.publish_component("20.1.6", dmg, archive, manifest)
                self.assertEqual(command.call_count, 2)


if __name__ == "__main__":
    unittest.main()
