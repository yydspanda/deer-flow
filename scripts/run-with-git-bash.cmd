@echo off
REM yyds: Windows 专用包装器。在 cmd.exe 里找不到 bash 的情况下，
REM       通过 git 的安装路径找到 Git Bash，用它来执行 .sh 脚本。
REM       因为 DeerFlow 的 .sh 脚本都需要 bash，Windows 原生不支持。
REM       用法：run-with-git-bash.cmd serve.sh
setlocal

set "bash_exe="

for /f "delims=" %%I in ('where git 2^>NUL') do (
    if exist "%%~dpI..\bin\bash.exe" (
        set "bash_exe=%%~dpI..\bin\bash.exe"
        goto :found_bash
    )
)

echo Could not locate Git for Windows Bash ("..\bin\bash.exe" relative to git on PATH). Ensure Git for Windows is installed and that git and bash.exe are available on PATH.
exit /b 1

:found_bash
echo Detected Windows - using Git Bash...
"%bash_exe%" %*
set "cmd_rc=%ERRORLEVEL%"
exit /b %cmd_rc%
