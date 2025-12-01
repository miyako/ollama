![version](https://img.shields.io/badge/version-20%2B-E23089)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm%20|%20win-64&color=blue)
[![license](https://img.shields.io/github/license/miyako/ollama)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/ollama/total)

# ollama
Local inference engine

**aknowledgements**: [ollama/ollama](https://github.com/ollama/ollama)

### Apple Silicon

```
cmake -S . -B build_arm -DUSE_ACCELERATE=ON 
cmake --build build_arm --config Release
GOARCH=arm64 GOOS=darwin go build -o ollama main.go
```

### Intel

```
cmake -S . -B build_amd
cmake --build build_amd --config Release
export CGO_ENABLED=1
GOARCH=amd64 GOOS=darwin go build -o ollama main.go
```

### Windows

```
cmake -S . -B build -A x64 -DUSE_AVX=ON -DUSE_AVX2=ON -DUSE_OPENMP=ON
cmake --build build --config Release
GOARCH=amd64 GOOS=windows go build -o ollama main.go
```
