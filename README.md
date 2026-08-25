# extra

PKGBUILDs for instantOS packages

## how this repo works

On every push to `main`, a GitHub Actions workflow
(`.github/workflows/build.yml`) builds all packages in parallel on
GitHub-hosted runners:

1. The `layers` job (`layers.py`) parses every PKGBUILD plus the AUR packages
   from `aurpackages` and computes the dependency layers — packages whose
   intra-repo dependencies are all satisfied by previous layers. The layers
   are computed per run; nothing is maintained by hand.
2. Each layer is built by a matrix job (`build-0` … `build-4`) where every
   package builds concurrently in its own `archlinux:base-devel` Docker
   container (`container_build.sh`). Layer-N jobs download the previous
   layers' packages into `repo/`, so dependencies resolve against them via a
   local `file://` repository.
3. The `assemble` job concatenates all build artifacts into the final
   repository database (`assemble.sh`) and publishes `repo/` to GitHub Pages:
   <https://instantos.github.io/extra/>

Old binaries are also mirrored at [instantos.surge.sh](https://instantos.surge.sh).

The per-job time limit of GitHub-hosted runners (6 h) is no obstacle in this
layout: it applies per job, and each job builds a single package. The slowest
package defines the wall-clock time, not the sum of all builds.

## requirements

- nothing special: GitHub-hosted `ubuntu-latest` runners (Docker and Python
  are preinstalled). A self-hosted runner works too, just change `runs-on`.
- to sign packages (see below):
  - repository secret `GPG_PRIVATE_KEY`
  - repository variable `GPG_KEYID`
  - public key material committed in `packages/instantos-keyring/`
- to sign packages (see below):
  - repository secret `GPG_PRIVATE_KEY`
  - repository variable `GPG_KEYID`
  - public key material committed in `packages/instantos-keyring/`

## package signing

Every package is signed (`*.pkg.tar.zst.sig` next to each package) and the
repository database is signed (`instant.db.tar.gz.sig`). Users can therefore
enable proper signature checking instead of `SigLevel = Never`.

Signing is active whenever `GPG_PRIVATE_KEY` is provided (the workflow
injects it from the repository secret into every build job and the assemble
job). Without it, the build falls back to unsigned packages. A
half-configured state (secret set but keyring files missing, or vice versa)
fails the build on purpose, and the `instantos-keyring` package is skipped
until real key material is committed.

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

`./build.sh` reproduces the full multi-pass CI build locally (Docker
required). To sign locally, export the same variables the CI uses:

```bash
GPG_PRIVATE_KEY="$(gpg --export-secret-subkeys --armor "$MASTER_FPR" | base64 -w0)" \
GPG_KEYID="$SIGNING_FPR" \
    ./build.sh
```

Single packages can be built without building everything else:

```bash
./build.sh packages/instantmenu    # local package
./build.sh aur/yay                 # AUR package
./build.sh --check                 # validate the signing configuration
```

`./assemble.sh` builds (and signs) the repository database from whatever
packages are in `repo/` — the same step the CI assemble job runs.
