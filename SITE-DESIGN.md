# Local inference documentation design

The shared guideline is maintained in
[miyako/local-inference-site-design](https://github.com/miyako/local-inference-site-design/blob/main/SITE-DESIGN.md).
Ollama adopts [v1.0.0](https://github.com/miyako/local-inference-site-design/blob/v1.0.0/SITE-DESIGN.md).

This is a documentation reference, not a submodule or build dependency.
Guideline updates are adopted explicitly alongside local implementation changes.

Local implementation: [site styles](assets/css/style.scss),
[syntax colors](assets/css/code.css), [layout](_layouts/default.html),
[home page](index.html) and [4D guide](guide.md). This reference is excluded
from generated Pages.

Site branding: `assets/ollama.png` is an unchanged copy of the 4D project's
`ollama/Resources/icon.png`, used for the sidebar, home-page engine icon, favicon, and structured
metadata. Accent: blue (llama.cpp/Swama palette).

The previous single-page documentation now lives in `guide.md` with its text
unchanged; links to its old anchors on the home page are forwarded to the guide.

Local exceptions: none beyond the shared guideline's permitted engine-specific
content and branding (Ollama, llama.cpp/MLX platforms, models pulled by name, endpoints, and release details).
