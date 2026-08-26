# instantos-keyring

Distributes the public key material that lets pacman verify signed instantOS
packages and repository databases.

Files (all vendored here, no external sources):

- `instantos.gpg` — exported public key(s): `gpg --export <MASTER_FPR>`
- `instantos-trusted` — master key fingerprint(s), one `<FPR>:4:` per line
- `instantos-revoked` — fingerprints of revoked keys

All three start out empty. The package deliberately fails to build while
`instantos.gpg` / `instantos-trusted` are still placeholders, and the CI skips
it until real key material is committed. See the root `README.md`, section
"Package signing", for the full runbook (generation, CI secrets, rotation).

After changing any of these files, bump `pkgver` in the `PKGBUILD`.

The package also installs and enables `instantos-keyring-wkd-sync.timer`. Once
a week it refreshes the already-trusted instantOS master key from the Web Key
Directory (WKD) for `hello@instantos.io`. This lets machines learn about new
signing subkeys before or after a rotation without trusting a new master key.

WKD must be published after initial key creation and whenever the public key
changes. From a clean directory, with the public key imported into GnuPG:

```bash
mkdir wkd
gpg --list-options show-only-fpr-mbox -k hello@instantos.io |
    gpg-wks-client --install-key -C wkd
```

Publish the generated files using WKD's advanced layout at:

```text
https://openpgpkey.instantos.io/.well-known/openpgpkey/instantos.io/
```

Copy the generated `wkd/instantos.io/policy` and `wkd/instantos.io/hu/` to that
URL; both must be present. Point `openpgpkey.instantos.io` at the HTTPS host
serving those files. Verify it from a machine without the key:

```bash
GNUPGHOME="$(mktemp -d)" gpg --auto-key-locate clear,nodefault,wkd \
    --locate-external-keys hello@instantos.io
```
