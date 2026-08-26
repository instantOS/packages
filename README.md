# extra

PKGBUILDs for instantOS packages

## how this repo works

On push to `main`, `.github/workflows/build.yml` builds all packages in
parallel:

1. `lint`: shfmt, `ty`, pytest (`lint.sh`)
2. `layers` (`layers.py`): computes dependency layers from all PKGBUILDs
   and `aurpackages` — each layer only depends on previous layers
3. `build-0` … `build-4`: one matrix job per layer, one unsigned package per job in
   an `archlinux:base-devel` container (`container_build.sh`); previous
   layers' packages are downloaded into `repo/` and resolved via a local
   `file://` repository
4. `assemble` (`assemble.sh`): signs the packages in an isolated container,
   builds and signs the repository database, and
   publishes `repo/` at <https://instantos.io/packages/>
   and Surge (<https://instantos.surge.sh>).

## package signing

Packages and the repository database are signed (`*.pkg.tar.zst.sig`,
`instant.db.tar.gz.sig`).

Signing is enabled in the assembly stage when `GPG_PRIVATE_KEY` is set;
without it, the assembled repository is unsigned. Package builds never receive
the private key: all package code finishes before a separate container, with
only `repo/` mounted, signs the artifacts and repository database. A secret
without matching keyring material fails the build, and `instantos-keyring` is
skipped until key material is committed.

### key layout

- **offline master key** (certify only, no expiry): stays on the
  workstation; its fingerprint is what users trust
- **passphrase-less signing subkey** (2 y expiry): exported to the CI
  secret; only this subkey leaks if a runner is compromised

### one-time setup

On the workstation (not the runner):

```bash
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key 'instantOS Packages <hello@instantos.io>' rsa3072 cert never
MASTER_FPR=$(gpg --with-colons --fingerprint hello@instantos.io | \
    awk -F: '$1 == "fpr" {print $10; exit}')

# dedicated signing subkey, valid for 2 years
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-add-key "$MASTER_FPR" rsa3072 sign 2y
SIGNING_FPR=$(gpg --with-colons --with-subkey-fingerprint \
    --list-keys "$MASTER_FPR" | awk -F: '$1 == "fpr" && ++n == 2 {print $10}')

# publish the public key so users can bootstrap trust
gpg --keyserver keyserver.ubuntu.com --send-keys "$MASTER_FPR"
```

Fill in the keyring package and commit (bump `pkgver` in its `PKGBUILD`):

```bash
cd packages/instantos-keyring
gpg --export "$MASTER_FPR" >instantos.gpg
echo "$MASTER_FPR:4:" >instantos-trusted
: >instantos-revoked
```

Publish that public key through WKD as described in
`packages/instantos-keyring/README.md`. WKD hosting requires an HTTPS
`openpgpkey.instantos.io` endpoint; it must be updated whenever a signing
subkey is added or revoked.

Repository configuration (GitHub → Settings → Secrets and variables → Actions):

- secret `GPG_PRIVATE_KEY`:
  `gpg --export-secret-subkeys --armor "$MASTER_FPR" | base64 -w0`
- variable `GPG_KEYID`: `$SIGNING_FPR`
- secret `SURGE_LOGIN`: your Surge account email address
- secret `SURGE_TOKEN`: your Surge authentication token (see below)

### surge deployment setup

To publish packages to [instantos.surge.sh](https://instantos.surge.sh), generate a Surge token locally:

```bash
# Log in to Surge (prompts for email and password / creates account if new)
npx surge login

# Retrieve your authentication token
npx surge token

# (Optional) Or generate a scoped token for the domain:
npx surge tokens add --domain instantos.surge.sh -m "github-actions"
```

Add your email as the `SURGE_LOGIN` secret and the output token as the `SURGE_TOKEN` secret in your GitHub repository settings.

### rotating and revoking keys

Rotate before the old signing subkey expires. Publish the new public subkey
through both the keyring package and WKD while CI still signs with the old
subkey; wait at least two weekly refresh windows before switching CI.

```bash
# issue a fresh signing subkey
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-add-key "$MASTER_FPR" rsa3072 sign 2y
# first re-export instantos.gpg, republish WKD, bump the keyring pkgver,
# and publish that keyring package using the OLD signing subkey
# after the overlap, update GPG_PRIVATE_KEY and GPG_KEYID to the new subkey

# revoke a compromised subkey, then also list it in instantos-revoked
gpg --quick-revoke-key "$MASTER_FPR" "$SIGNING_FPR" compromised
```

Rotation requires no user action.

## using the repository

`/etc/pacman.conf`:

```ini
[instant]
SigLevel = Required TrustedOnly
Include = /etc/pacman.d/instantmirrorlist
```

Bootstrap trust once (verify the fingerprint against a trusted source first):

```bash
pacman-key --recv-keys <MASTER_FPR>
pacman-key --lsign-key <MASTER_FPR>
```

or install the `instantos-keyring` package (`pacman-key --populate instantos`).

Fresh instantOS installation media must include and populate
`instantos-keyring` before enabling `Required TrustedOnly`; the dependency
keeps an established trust root updated but cannot bootstrap it from a
repository whose signatures are not trusted yet.

The `instantos` metapackage depends on both `instantos-keyring` and
`instantos-mirrorlist`. New installations therefore receive the trust material
and a package-owned `/etc/pacman.d/instantmirrorlist`; future key and mirror
changes arrive through normal package upgrades. The file is tracked as a
pacman backup file, so local edits are preserved as `.pacnew` changes and are
visible to `pacdiff`. Existing systems keep their current `SigLevel` setting;
installing these packages does not enable mandatory signature checking.

`instantos-keyring-wkd-sync.timer`, enabled by the keyring package, refreshes
the existing instantOS master key from `hello@instantos.io` via WKD once a
week. See `packages/instantos-keyring/README.md` for the required WKD hosting
setup.

## building locally

requires instantOS/instantTOOLS to be installed

### build all packages

`ibuild fullrepo`

this builds all pkgbuilds from source and also builds all AUR packages from the ./aurpackages file.
This does not generate a package database nor does it publish anything

`./build.sh` reproduces the full multi-pass CI build locally (Docker
required). To sign locally, export the same variables the CI uses:

```bash
GPG_PRIVATE_KEY="$(gpg --export-secret-subkeys --armor "$MASTER_FPR" | base64 -w0)" \
GPG_KEYID="$SIGNING_FPR" \
    ./build.sh
```

Single-package builds:

```bash
./build.sh packages/instantmenu    # local package
./build.sh aur/yay                 # AUR package
./build.sh --check                 # validate the signing configuration
```

`./assemble.sh` signs the packages and builds (and signs) the repository
database from the packages in `repo/`.

`just check` / `./lint.sh`: shfmt, `ty`, pytest.
