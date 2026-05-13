# CI Diagnostics - Run 25778052798

- SHA: a228df9a73855a53634e8f6c1a4f9b7125a9cd47
- Version: 0.2.18
- Job status: success
- HAVE_UPDATER: false
- Time: 2026-05-13T04:36:33Z

## collect-diag.txt
```
PWD: D:\a\VoxRad\VoxRad\desktop
bundleAbs: D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle
outAbs: D:\a\VoxRad\VoxRad\desktop\release-artifacts
bundle exists: True
Files in bundle (4):
  RadSpeed_0.2.18_x64_en-US.msi :: D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\msi\RadSpeed_0.2.18_x64_en-US.msi (3170304)
  RadSpeed_0.2.18_x64_en-US.msi.sig :: D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\msi\RadSpeed_0.2.18_x64_en-US.msi.sig (420)
  RadSpeed_0.2.18_x64-setup.exe :: D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\nsis\RadSpeed_0.2.18_x64-setup.exe (2259726)
  RadSpeed_0.2.18_x64-setup.exe.sig :: D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\nsis\RadSpeed_0.2.18_x64-setup.exe.sig (420)
All .sig files (2):
  D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\msi\RadSpeed_0.2.18_x64_en-US.msi.sig
  D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\nsis\RadSpeed_0.2.18_x64-setup.exe.sig
Pattern '*-setup.exe' matched 1 files
  copied: RadSpeed_0.2.18_x64-setup.exe -> D:\a\VoxRad\VoxRad\desktop\release-artifacts\RadSpeed_0.2.18_x64-setup.exe
Pattern '*-setup.exe.sig' matched 1 files
  copied: RadSpeed_0.2.18_x64-setup.exe.sig -> D:\a\VoxRad\VoxRad\desktop\release-artifacts\RadSpeed_0.2.18_x64-setup.exe.sig
Pattern '*_en-US.msi' matched 1 files
  copied: RadSpeed_0.2.18_x64_en-US.msi -> D:\a\VoxRad\VoxRad\desktop\release-artifacts\RadSpeed_0.2.18_x64_en-US.msi
Pattern '*_en-US.msi.sig' matched 1 files
  copied: RadSpeed_0.2.18_x64_en-US.msi.sig -> D:\a\VoxRad\VoxRad\desktop\release-artifacts\RadSpeed_0.2.18_x64_en-US.msi.sig
Pattern '*.nsis.zip' matched 0 files
Pattern '*.nsis.zip.sig' matched 0 files
Collected (4):
  RadSpeed_0.2.18_x64_en-US.msi (3170304 bytes)
  RadSpeed_0.2.18_x64_en-US.msi.sig (420 bytes)
  RadSpeed_0.2.18_x64-setup.exe (2259726 bytes)
  RadSpeed_0.2.18_x64-setup.exe.sig (420 bytes)
exeFile: RadSpeed_0.2.18_x64-setup.exe
sigFile: RadSpeed_0.2.18_x64-setup.exe.sig
update.json gen failed: A parameter cannot be found that matches parameter name 'UTC'.
```

## bundle-tree.txt
```
RUNNER_TEMP=D:\a\_temp
PWD=/d/a/VoxRad/VoxRad/desktop

Bundle directory: src-tauri/target/release/bundle
Tree of src-tauri/target/release/bundle:
src-tauri/target/release/bundle/msi/RadSpeed_0.2.18_x64_en-US.msi 3170304
src-tauri/target/release/bundle/msi/RadSpeed_0.2.18_x64_en-US.msi.sig 420
src-tauri/target/release/bundle/nsis/RadSpeed_0.2.18_x64-setup.exe 2259726
src-tauri/target/release/bundle/nsis/RadSpeed_0.2.18_x64-setup.exe.sig 420

--- recursive find for setup/sig files anywhere ---
./src-tauri/target/release/bundle/msi/RadSpeed_0.2.18_x64_en-US.msi 3170304
./src-tauri/target/release/bundle/msi/RadSpeed_0.2.18_x64_en-US.msi.sig 420
./src-tauri/target/release/bundle/nsis/RadSpeed_0.2.18_x64-setup.exe 2259726
./src-tauri/target/release/bundle/nsis/RadSpeed_0.2.18_x64-setup.exe.sig 420

--- tauri-build.log tail (last 100) ---
       Debug [rustls::client::hs] ALPN protocol is None
       Debug [ureq_proto::client] Call<RecvResponse>
       Debug [ureq_proto::client] Call<RecvBody>
       Debug [ureq::run] Response { status: 200, version: HTTP/1.1, headers: {"connection": "keep-alive", "content-length": "34304", "server": "Windows-Azure-Blob/1.0 Microsoft-HTTPAPI/2.0", "date": "Wed, 13 May 2026 04:36:19 GMT", "content-type": "application/octet-stream", "<NOTICE>": "18 HEADERS ARE REDACTED"} }
       Debug [ureq_proto::client] Call<Cleanup>
       Debug [ureq::pool] Return to pool: PoolKey { scheme: "https", authority: release-assets.githubusercontent.com, proxy: None }
        Info [tauri_bundler::utils::http_utils] validating hash
        Info [tauri_bundler::bundle::windows::nsis] Target: x64
     Running [tauri_bundler::bundle::windows::nsis] makensis to produce D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\nsis\RadSpeed_0.2.18_x64-setup.exe
     Running [tauri_bundler::utils] Command `C:\Users\runneradmin\AppData\Local\tauri\NSIS\makensis.exe  -INPUTCHARSET UTF8 -OUTPUTCHARSET UTF8 -V3 D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\nsis\x64\installer.nsi`
MakeNSIS v3.11 - Copyright 1999-2025 Contributors
See the file COPYING for license details.
Credits can be found in the Users Manual.

Processing config: C:\Users\runneradmin\AppData\Local\tauri\NSIS\nsisconf.nsh
Processing script file: "D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\nsis\x64\installer.nsi" (UTF8)

Processed 1 file, writing output (x86-unicode):

Output: "D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\nsis\x64\nsis-output.exe"
Install: 6 pages (384 bytes), 3 sections (6216 bytes), 1854 instructions (51912 bytes), 393 strings (14582 bytes), 1 language table (414 bytes).
Uninstall: 2 pages (192 bytes), 1 section (2072 bytes), 615 instructions (17220 bytes), 195 strings (7274 bytes), 1 language table (274 bytes).
Datablock optimizer saved 26498 bytes (~0.4%).

Using lzma (compress whole) compression.

EXE header size:               53248 / 38912 bytes
Install code:                          (73972 bytes)
Install data:                          (6473630 bytes)
Uninstall code+data:                   (93632 bytes)
Compressed data:             2206474 / 6641234 bytes
CRC (0xFD9F0B26):                  4 / 4 bytes

Total size:                  2259726 / 6680150 bytes (33.8%)
        Info [tauri_bundler::bundle] Patching D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\radspeed.exe with bundle type information: msi
        Info [tauri_bundler::bundle::windows::msi] Verifying wix package
 Downloading [tauri_bundler::utils::http_utils] https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip
       Debug [ureq_proto::client] Call<Prepare>
       Debug [ureq::run] GET https://github.com/******
       Debug [ureq::unversioned::resolver] Resolved: ArrayVec { len: 1, arr: [140.82.114.4:443] }
       Debug [ureq::unversioned::transport::tcp] Connected TcpStream to 140.82.114.4:443
       Debug [rustls::client::hs] No cached session for DnsName("github.com")
       Debug [rustls::client::hs] Not resuming any session
       Debug [ureq::tls::rustls] Wrapped TLS
       Debug [ureq_proto::client] Call<SendRequest>
       Debug [ureq::run] Request { method: GET, uri: https://github.com/******, version: HTTP/1.1, headers: {"accept-encoding": "gzip", "user-agent": "tauri-bundler/2.9.1", "accept": "*/*", "host": "github.com"} }
       Debug [rustls::client::hs] Using ciphersuite TLS13_AES_128_GCM_SHA256
       Debug [rustls::client::tls13] Not resuming
       Debug [rustls::client::tls13] TLS1.3 encrypted extensions: ServerExtensions { server_name_ack: (), unknown_extensions: {}, .. }
       Debug [rustls::client::hs] ALPN protocol is None
       Debug [ureq_proto::client] Call<RecvResponse>
       Debug [ureq_proto::client::recvresp] Partial redirection response, insert fake connection: close
       Debug [ureq_proto::client] Call<Redirect>
       Debug [ureq::run] Response { status: 302, version: HTTP/1.1, headers: {"date": "Wed, 13 May 2026 04:36:22 GMT", "content-type": "text/html; charset=utf-8", "content-length": "0", "connection": "close", "location": "******", "<NOTICE>": "7 HEADERS ARE REDACTED"} }
       Debug [ureq::pool] Close: PoolKey { scheme: "https", authority: github.com, proxy: None }
       Debug [ureq_proto::client] Call<Prepare>
       Debug [ureq::run] Redirect (302 Found): GET https://release-assets.githubusercontent.com/******
       Debug [ureq::run] GET https://release-assets.githubusercontent.com/******
       Debug [ureq::unversioned::resolver] Resolved: ArrayVec { len: 4, arr: [185.199.110.133:443, 185.199.111.133:443, 185.199.108.133:443, 185.199.109.133:443] }
       Debug [ureq::unversioned::transport::tcp] Connected TcpStream to 185.199.110.133:443
       Debug [rustls::client::hs] No cached session for DnsName("release-assets.githubusercontent.com")
       Debug [rustls::client::hs] Not resuming any session
       Debug [ureq::tls::rustls] Wrapped TLS
       Debug [ureq_proto::client] Call<SendRequest>
       Debug [ureq::run] Request { method: GET, uri: https://release-assets.githubusercontent.com/******, version: HTTP/1.1, headers: {"accept-encoding": "gzip", "user-agent": "tauri-bundler/2.9.1", "accept": "*/*", "host": "release-assets.githubusercontent.com"} }
       Debug [rustls::client::hs] Using ciphersuite TLS13_AES_128_GCM_SHA256
       Debug [rustls::client::tls13] Not resuming
       Debug [rustls::client::tls13] TLS1.3 encrypted extensions: ServerExtensions { server_name_ack: (), unknown_extensions: {}, .. }
       Debug [rustls::client::hs] ALPN protocol is None
       Debug [ureq_proto::client] Call<RecvResponse>
       Debug [ureq_proto::client] Call<RecvBody>
       Debug [ureq::run] Response { status: 200, version: HTTP/1.1, headers: {"connection": "keep-alive", "content-length": "41297555", "server": "Windows-Azure-Blob/1.0 Microsoft-HTTPAPI/2.0", "date": "Wed, 13 May 2026 04:36:22 GMT", "content-type": "application/octet-stream", "<NOTICE>": "18 HEADERS ARE REDACTED"} }
       Debug [ureq_proto::client] Call<Cleanup>
       Debug [ureq::pool] Return to pool: PoolKey { scheme: "https", authority: release-assets.githubusercontent.com, proxy: None }
        Info [tauri_bundler::utils::http_utils] validating hash
        Info [tauri_bundler::bundle::windows::msi] extracting WIX
        Info [tauri_bundler::bundle::windows::msi] Target: x64
     Running [tauri_bundler::bundle::windows::msi] candle for "D:\\a\\VoxRad\\VoxRad\\desktop\\src-tauri\\target\\release\\wix\\x64\\main.wxs"
     Running [tauri_bundler::utils] Command `C:\Users\runneradmin\AppData\Local\tauri\WixTools314\candle.exe  -arch x64 D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\main.wxs -dSourceDir=D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\radspeed.exe`
Windows Installer XML Toolset Compiler version 3.14.1.8722
Copyright (c) .NET Foundation and contributors. All rights reserved.

main.wxs
     Running [tauri_bundler::bundle::windows::msi] light to produce D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\msi\RadSpeed_0.2.18_x64_en-US.msi
     Running [tauri_bundler::utils] Command `C:\Users\runneradmin\AppData\Local\tauri\WixTools314\light.exe  -ext C:\Users\runneradmin\AppData\Local\tauri\WixTools314\WixUIExtension.dll -ext C:\Users\runneradmin\AppData\Local\tauri\WixTools314\WixUtilExtension.dll -o D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\output.msi -cultures:en-us -loc D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\locale.wxl *.wixobj`
Windows Installer XML Toolset Linker version 3.14.1.8722
Copyright (c) .NET Foundation and contributors. All rights reserved.

D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\main.wxs(198) : warning LGHT1076 : ICE03: String overflow (greater than length permitted in column); Table: CustomAction, Column: Target, Key(s): DownloadAndInvokeBootstrapper
D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\main.wxs(33) : warning LGHT1076 : ICE40: REINSTALLMODE is defined in the Property table. This may cause difficulties.
D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\main.wxs(120) : warning LGHT1076 : ICE57: Component 'CMP_UninstallShortcut' has both per-user and per-machine data with an HKCU Registry KeyPath.
D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\wix\x64\main.wxs(40) : warning LGHT1076 : ICE61: This product should remove only older versions of itself. No Maximum version was detected for the current product. (WIX_UPGRADE_DETECTED)
    Finished [tauri_bundler::bundle] 2 bundles at:
        D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\nsis\RadSpeed_0.2.18_x64-setup.exe
        D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\msi\RadSpeed_0.2.18_x64_en-US.msi

    Finished [tauri_cli::bundle] 2 updater signatures at:
        D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\nsis\RadSpeed_0.2.18_x64-setup.exe.sig
        D:\a\VoxRad\VoxRad\desktop\src-tauri\target\release\bundle\msi\RadSpeed_0.2.18_x64_en-US.msi.sig

```
