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
