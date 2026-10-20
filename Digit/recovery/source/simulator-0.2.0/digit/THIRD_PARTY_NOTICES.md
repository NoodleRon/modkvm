# Third-party provenance

This checkpoint contains original project code and uses the Python standard
library at runtime. No PiKVM, TinyPilot, JetKVM, HIDAgent, TinyUSB, or Raspberry Pi
SDK source has been copied, vendored, forked, or incorporated.

`setuptools` is declared only as a Python packaging build requirement. It is not
vendored. The quick-start commands and tests do not need packaging or a network
connection.

The external projects linked in the architecture document are integration
candidates, not included dependencies. Before reusing any code:

1. Pin its exact source revision and inspect all applicable licenses
2. Preserve copyright, license, NOTICE, and attribution files
3. Review bundled and transitive components separately
4. Document modifications and comply with any source-distribution obligations
5. Do not relabel upstream code as original MIT code

The current LICENSE covers original Digit code only. Confirm project ownership
and copyright attribution before public release.

## Original observation package composition

The original MIT-licensed Digit observation package is included in this isolated
composition. Its license is retained at docs/observation/LICENSE; its accepted
source modules are unchanged. The accepted controller implementation modules are also
unchanged; package-version metadata advances for this composition. New loop modules are original Digit code under the same MIT license.
