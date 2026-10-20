CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -Wpedantic -O2
CPPFLAGS += -Iinclude
.PHONY: test sanitize clean native-probe
build:
	mkdir -p build
build/test_hid: src/digit_hid.c include/digit_hid.h tests/test_hid.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) src/digit_hid.c tests/test_hid.c -o $@
test: build/test_hid
	./build/test_hid
	python3 -m unittest discover -s tests -p 'test_*.py' -v
sanitize: | build
	$(CC) $(CPPFLAGS) -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -fno-omit-frame-pointer src/digit_hid.c tests/test_hid.c -o build/test_hid_sanitize
	./build/test_hid_sanitize
native-probe: | build
	@test "$$(uname -s)" = Darwin || { echo 'Native build requires macOS + Xcode SDK; not compiled here'; exit 1; }
	xcrun clang -std=c11 -Iinclude -c src/digit_hid.c -o build/digit_hid.o
	xcrun clang -fobjc-arc -Wall -Wextra -Werror -Iinclude -Inative -framework Foundation -framework IOBluetooth native/DigitClassicSDP.m native/probe.m build/digit_hid.o -o build/digit-classic-probe
clean:
	rm -rf build
