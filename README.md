# extra

PKGBUILDs for instantOS packages

## how this repo works

On every push to `main`, a GitHub Actions workflow (`.github/workflows/build.yml`)
builds all packages on a self-hosted runner:

1. `build.sh` collects the local `packages/*/` PKGBUILDs plus the AUR packages
   listed in `aurpackages` and builds each one in a throwaway
   `archlinux:base-devel` Docker container (`container_build.sh`).
2. Packages, signatures and the `repo-add` database accumulate in `repo/`.
3. The contents of `repo/` are published to GitHub Pages:
   <https://instantos.github.io/extra/>

Old binaries are also mirrored at [instantos.surge.sh](https://instantos.surge.sh).

## requirements

- a GitHub Actions runner with Docker. The workflow as configured uses a
  self-hosted runner (`runs-on: self-hosted`, 48 h timeout): a full build of
  all packages far exceeds the 6 h limit of GitHub-hosted runners.
- to sign packages (see below):
  - repository secret `GPG_PRIVATE_KEY`
  - repository variable `GPG_KEYID`
  - public key material committed in `packages/instantos-keyring/`

## package signing

Every package is signed (`*.pkg.tar.zst.sig` next to each package) and the
repository database is signed (`instant.db.tar.gz.sig`). Users can therefore
enable proper signature checking instead of `SigLevel = Never`.

Signing is active whenever `GPG_PRIVATE_KEY` is provided to `build.sh` (the
workflow injects it from the repository secret). Without it, the build falls
back to unsigned packages. A half-configured state (secret set but keyring
files missing, or vice versa) fails the build on purpose, and the
`instantos-keyring` package is skipped until real key material is committed.

### key layout

- An **offline master key** (certify only) — never leaves your workstation,
  never expires. Its fingerprint is what users ultimately trust.
- A **passphrase-less signing subkey** with an expiry, exported into the CI
  secret. If the runner is ever compromised, only this revocable subkey leaks.

### one-time setup

On your workstation (not the runner):

```bash
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key 'instantOS Packages <hello@instantos.io>' cert never
MASTER_FPR=$(gpg --list-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')

# dedicated signing subkey, valid for 2 years
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-add-key "$MASTER_FPR" rsa3072 sign 2y
SIGNING_FPR=$(gpg --list-keys --with-colons | awk -F: '/^fpr:/ {f[++n]=$10} END {print f[2]}')

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

Configure the repository (GitHub → Settings → Secrets and variables → Actions):

- secret `GPG_PRIVATE_KEY`:
  `gpg --export-secret-subkeys --armor "$MASTER_FPR" | base64 -w0`
- variable `GPG_KEYID`: `$SIGNING_FPR`

The next push to `main` builds a fully signed repository.

### rotating and revoking keys

```bash
# issue a fresh signing subkey
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-add-key "$MASTER_FPR" rsa3072 sign 2y
# then update the GPG_PRIVATE_KEY secret and GPG_KEYID variable, re-export
# instantos.gpg and bump the keyring pkgver

# revoke a compromised subkey, then also list it in instantos-revoked
gpg --quick-revoke-key "$MASTER_FPR" "$SIGNING_FPR" compromised
```

Rotation needs no action from users; the master key they trust stays the same.

## using the repository

`/etc/pacman.conf`:

```ini
[instant]
SigLevel = Required TrustedOnly
Server = https://instantos.github.io/extra/
```

Bootstrap trust once (verify the fingerprint against a trusted source first):

```bash
pacman-key --recv-keys <MASTER_FPR>
pacman-key --lsign-key <MASTER_FPR>
```

or install the `instantos-keyring` package, which does the equivalent via
`pacman-key --populate instantos`.

## building locally

requires instantOS/instantTOOLS to be installed

### build all packages

`ibuild fullrepo`

this builds all pkgbuilds from source and also builds all AUR packages from the ./aurpackages file.
This does not generate a package database nor does it publish anything

`./build.sh` reproduces the CI build (Docker required) and generates the
repository database in `repo/`. To sign locally, export the same variables the
CI uses:

```bash
GPG_PRIVATE_KEY="$(gpg --export-secret-subkeys --armor "$MASTER_FPR" | base64 -w0)" \
GPG_KEYID="$SIGNING_FPR" \
    ./build.sh
```
