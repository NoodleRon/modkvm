@echo off
setlocal
cd /d "%~dp0\.."
if not exist build mkdir build
cl /nologo /std:c++17 /EHsc /W4 /WX /MT /LD /I Core Core\digit_queue.cpp /Febuild\digit_queue.dll /Fobuild\digit_queue.obj
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /W4 /WX /MT /I Core Core\digit_queue.cpp Tests\test_queue.cpp /Febuild\test_queue.exe /Fobuild\
if errorlevel 1 exit /b 1
build\test_queue.exe
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /W4 /WX /MT /I Core Core\digit_queue.cpp Tests\test_wrap.cpp /Febuild\test_wrap.exe /Fobuild\
if errorlevel 1 exit /b 1
build\test_wrap.exe
