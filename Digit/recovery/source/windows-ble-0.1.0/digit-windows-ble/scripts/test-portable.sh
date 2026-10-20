#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p build
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -I Core Core/digit_queue.cpp Tests/test_queue.cpp -o build/test_queue
./build/test_queue
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -fsanitize=address,undefined -fno-omit-frame-pointer -g -I Core Core/digit_queue.cpp Tests/test_queue.cpp -o build/test_queue_sanitized
ASAN_OPTIONS=detect_leaks=0 ./build/test_queue_sanitized
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -fsanitize=address,undefined -fno-omit-frame-pointer -g -I Core Core/digit_queue.cpp Tests/test_wrap.cpp -o build/test_wrap_sanitized
ASAN_OPTIONS=detect_leaks=0 ./build/test_wrap_sanitized
python3 -m unittest discover -s Tests -p 'test_*.py' -v
