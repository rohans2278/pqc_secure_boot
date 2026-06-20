# RSA → ML-DSA-44 Integration Reference

> **Purpose.** This is the authoritative, verbatim description of *exactly* how
> RSA FIT verification was replaced with post-quantum **ML-DSA-44** in the proven
> proof-of-concept U-Boot tree. It is the ground-truth context that
> `pqc-boot generate-patch` consumes to (re)produce
> `patches/uboot-2026.04-mldsa44.diff`, and that the build/sign/deploy stages rely
> on. Everything here was extracted **read-only** from the PoC artifacts and
> cross-checked against a clean `v2026.04` clone. Do not guess against this doc —
> if something here disagrees with the real source, the source wins.

> **Branding.** This document uses the **target `pqc-boot` naming** throughout. The
> PoC tree was branded `quboot`; see [§11 Branding rename map](#11-branding-rename-map)
> for the exact tokens that must be renamed when reconstructing the patch and boot
> script. Code identifiers and DT property names are **not** branded and stay as-is.

---

## 1. Scope & trust boundary

pqc-boot converts **one** link in the Pi 5 boot chain: U-Boot's verification of the
next stage (the kernel **FIT** image) is changed from **RSA** to **ML-DSA-44**.

- **In scope:** U-Boot → kernel FIT signature verification (RSA → ML-DSA-44).
- **Out of scope:** the Broadcom EEPROM bootloader, which is RSA-signed by Raspberry
  Pi and not user-replaceable. It remains the RSA root of trust. The chain is **not**
  fully quantum-safe; do not overstate it.

**Pins:** U-Boot tag `v2026.04`; ML-DSA parameter set **44**.

---

## 2. ML-DSA-44 facts (constants)

| Quantity | Value | Source of truth |
|----------|-------|-----------------|
| Public key | **1312 bytes** | `MLDSA44_PUBLICKEYBYTES` |
| Secret key | **2560 bytes** | `MLDSA44_SECRETKEYBYTES` |
| Signature | **2420 bytes** | `MLDSA44_BYTES` |

- Keys are **raw binary** (native mldsa-native packing — not PEM/DER), named
  `<keyname>.pub` (public, 1312 B) and `<keyname>.bin` (private, 2560 B) in the keydir.
  PoC sample: `~/keys/mykey.pub` (1312 B), `~/keys/mykey.bin` (2560 B).
- Parameter set 44 is selected by `MLD_CONFIG_PARAMETER_SET=44` in
  `lib/ml-dsa/mldsa_native_config.h` (vendored copy of the upstream config header).
- Core API (SUPERCOP naming, from `mldsa_native.h`):
  - host sign: `crypto_sign_signature(sig, &siglen, m, mlen, ctx, ctxlen, sk)`
  - target verify: `crypto_sign_verify(sig, siglen, m, mlen, ctx, ctxlen, pk)`
  - both are called with `ctx=NULL, ctxlen=0` in U-Boot (see §5/§6).

---

## 3. Algorithm registration & the algo string

**FIT algorithm string:** `sha256,mldsa44` — a `sha256` checksum over the image
regions, then an `mldsa44` crypto signature over that hash.

**How the name resolves (VERIFIED against the clean `v2026.04` tree,
`.pqcboot-work/u-boot/boot/image-sig.c`):** `image_get_crypto_algo()` splits the
algo string on the comma and looks the crypto name up via a **linker list**, not a
static array:

```c
struct crypto_algo *image_get_crypto_algo(const char *full_name)
{
	struct crypto_algo *crypto, *end;
	const char *name;

	/* Move name to after the comma */
	name = strchr(full_name, ',');
	if (!name)
		return NULL;
	name += 1;

	crypto = ll_entry_start(struct crypto_algo, cryptos);
	end = ll_entry_end(struct crypto_algo, cryptos);
	for (; crypto < end; crypto++) {
		if (!strcmp(crypto->name, name))
			return crypto;
	}

	/* Not found */
	return NULL;
}
```

The clean `v2026.04` tree contains **zero** ml-dsa references. Consequently:

- **Target side (`boot/image-sig.c`):** the **only** edit needed is adding
  `#include <u-boot/ml-dsa.h>` after the `<u-boot/rsa.h>` include. Registration
  happens entirely through the `U_BOOT_CRYPTO_ALGO(mldsa44)` linker-list entry
  (below) — **no array/table is edited** in this file.

  Verbatim entry, from `lib/ml-dsa/mldsa-verify.c`:
  ```c
  #ifndef USE_HOSTCC
  U_BOOT_CRYPTO_ALGO(mldsa44) = {
  	.name = "mldsa44",
  	.key_len = MLDSA44_PUBLICKEYBYTES,
  	.verify = mldsa_verify,
  };
  #endif
  ```

- **Host side (`tools/image-sig-host.c`):** mkimage does **not** use the linker
  list; it has a static `crypto_algos[]` array, so an `mldsa44` entry **is** appended
  to that array (after the ecdsa/secp521r1 entries), plus `#include <u-boot/ml-dsa.h>`.

  Verbatim entry, from `tools/image-sig-host.c`:
  ```c
  	{
  		.name = "mldsa44",
  		.key_len = MLDSA44_PUBLICKEYBYTES,
  		.sign = mldsa_sign,
  		.add_verify_data = mldsa_add_verify_data,
  		.verify = mldsa_verify,
  	},
  ```
  Anchor (the entry immediately preceding it in the PoC array):
  ```c
  	{
  		.name = "secp521r1",
  		.key_len = ECDSA521_BYTES,
  		.sign = ecdsa_sign,
  		.add_verify_data = ecdsa_add_verify_data,
  		.verify = ecdsa_verify,
  	},
  ```

---

## 4. Public-key delivery into the FIT (`mldsa_add_verify_data`)

The ML-DSA-44 public key is written into the control DTB's `/signature/key-<name>`
node as device-tree properties:

| Property | Value |
|----------|-------|
| `key-name-hint` (`FIT_KEY_HINT`) | the keyname |
| **`mldsa,public-key`** | raw public key, **1312 B** |
| `algo` (`FIT_ALGO_PROP`) | `info->name`, i.e. the registered algo (`mldsa44`) |
| `required` (`FIT_KEY_REQUIRED`) | optional, if `info->require_keys` set |

Verbatim, from `lib/ml-dsa/mldsa-sign.c` (host/mkimage only):

```c
int mldsa_add_verify_data(struct image_sign_info *info, void *keydest)
{
	uint8_t pk[MLDSA44_PUBLICKEYBYTES];
	char path[1024];
	int parent, node = -FDT_ERR_NOTFOUND;
	char name[100];
	int ret;

	/* Build path to public key file */
	if (info->keydir && info->keyname) {
		snprintf(path, sizeof(path), "%s/%s.pub",
			 info->keydir, info->keyname);
	} else {
		fprintf(stderr, "ML-DSA: no keydir/keyname specified\n");
		return -1;
	}

	/* Read public key */
	ret = mldsa_read_key(path, pk, MLDSA44_PUBLICKEYBYTES);
	if (ret)
		return ret;

	/* Find or create /signature node */
	parent = fdt_subnode_offset(keydest, 0, FIT_SIG_NODENAME);
	if (parent == -FDT_ERR_NOTFOUND) {
		parent = fdt_add_subnode(keydest, 0, FIT_SIG_NODENAME);
		if (parent < 0) {
			if (parent != -FDT_ERR_NOSPACE)
				fprintf(stderr, "ML-DSA: couldn't create "
					"signature node: %s\n",
					fdt_strerror(parent));
			return parent == -FDT_ERR_NOSPACE ? -ENOSPC : -EIO;
		}
	}

	/* Find or create key-<name> subnode */
	snprintf(name, sizeof(name), "key-%s", info->keyname);
	node = fdt_subnode_offset(keydest, parent, name);
	if (node == -FDT_ERR_NOTFOUND) {
		node = fdt_add_subnode(keydest, parent, name);
		if (node < 0) {
			if (node != -FDT_ERR_NOSPACE)
				fprintf(stderr, "ML-DSA: couldn't create "
					"key subnode: %s\n",
					fdt_strerror(node));
			return node == -FDT_ERR_NOSPACE ? -ENOSPC : -EIO;
		}
	}

	/* Write properties */
	ret = fdt_setprop_string(keydest, node, FIT_KEY_HINT, info->keyname);
	if (!ret)
		ret = fdt_setprop(keydest, node, "mldsa,public-key",
				  pk, MLDSA44_PUBLICKEYBYTES);
	if (!ret)
		ret = fdt_setprop_string(keydest, node, FIT_ALGO_PROP,
					 info->name);
	if (!ret && info->require_keys)
		ret = fdt_setprop_string(keydest, node, FIT_KEY_REQUIRED,
					 info->require_keys);

	if (ret)
		return ret == -FDT_ERR_NOSPACE ? -ENOSPC : -EIO;

	return node;
}
```

---

## 5. Verify path (`mldsa_verify` + `mldsa_verify_with_keynode`)

At boot, U-Boot hashes the image regions with sha256, reads `mldsa,public-key` from
`info->fdt_blob` (the control DTB — see §10 for *which* DTB this actually is on the
Pi 5), validates the 1312/2420 lengths, then calls `crypto_sign_verify`.

Verbatim, from `lib/ml-dsa/mldsa-verify.c`:

```c
#if CONFIG_IS_ENABLED(FIT_SIGNATURE)
static int mldsa_verify_with_keynode(struct image_sign_info *info,
				     const void *hash, uint8_t *sig,
				     uint sig_len, int node)
{
	const void *blob = info->fdt_blob;
	const uint8_t *pk;
	int pk_len;
	int ret;
	const char *algo;

	if (node < 0) {
		debug("%s: Skipping invalid node\n", __func__);
		return -EBADF;
	}

	algo = fdt_getprop(blob, node, FIT_ALGO_PROP, NULL);
	if (!algo) {
		debug("%s: Missing 'algo' property\n", __func__);
		return -EFAULT;
	}

	if (strcmp(info->name, algo)) {
		debug("%s: Wrong algo: have %s, expected %s\n", __func__,
		      info->name, algo);
		return -EFAULT;
	}

	pk = fdt_getprop(blob, node, "mldsa,public-key", &pk_len);
	if (!pk) {
		debug("%s: Missing 'mldsa,public-key' property\n", __func__);
		return -EFAULT;
	}

	if (pk_len != MLDSA44_PUBLICKEYBYTES) {
		debug("%s: Invalid public key length %d, expected %d\n",
		      __func__, pk_len, MLDSA44_PUBLICKEYBYTES);
		return -EINVAL;
	}

	if (sig_len != MLDSA44_BYTES) {
		debug("%s: Invalid signature length %d, expected %d\n",
		      __func__, sig_len, MLDSA44_BYTES);
		return -EINVAL;
	}

	ret = crypto_sign_verify(sig, sig_len,
				 hash, info->checksum->checksum_len,
				 NULL, 0,
				 pk);
	if (ret) {
		debug("%s: ML-DSA verification failed\n", __func__);
		return -EACCES;
	}

	return 0;
}
#endif /* CONFIG_IS_ENABLED(FIT_SIGNATURE) */

int mldsa_verify(struct image_sign_info *info,
		 const struct image_region region[], int region_count,
		 uint8_t *sig, uint sig_len)
{
	uint8_t hash[info->checksum->checksum_len];
	int ret;

	/* Calculate SHA256 hash of image regions */
	ret = info->checksum->calculate(info->checksum->name,
					region, region_count, hash);
	if (ret < 0) {
		debug("%s: Error in checksum calculation\n", __func__);
		return -EINVAL;
	}

#if CONFIG_IS_ENABLED(FIT_SIGNATURE)
	{
		const void *blob = info->fdt_blob;
		int ndepth, noffset;
		int sig_node, node;
		char name[100];

		sig_node = fdt_subnode_offset(blob, 0, FIT_SIG_NODENAME);
		if (sig_node < 0) {
			debug("%s: No signature node found\n", __func__);
			return -ENOENT;
		}

		/* Try the hinted key first */
		snprintf(name, sizeof(name), "key-%s", info->keyname);
		node = fdt_subnode_offset(blob, sig_node, name);
		ret = mldsa_verify_with_keynode(info, hash, sig, sig_len, node);
		if (!ret)
			return 0;

		debug("%s: Could not verify key '%s', trying all\n",
		      __func__, name);

		/* Try all available keys */
		for (ndepth = 0,
		     noffset = fdt_next_node(blob, sig_node, &ndepth);
		     (noffset >= 0) && (ndepth > 0);
		     noffset = fdt_next_node(blob, noffset, &ndepth)) {
			if (ndepth == 1 && noffset != node) {
				ret = mldsa_verify_with_keynode(info, hash,
								sig, sig_len,
								noffset);
				if (!ret)
					return 0;
			}
		}
	}
#endif
	debug("%s: Failed to verify by any means\n", __func__);
	return -EACCES;
}
```

---

## 6. Sign path (`mldsa_sign`)

Host/mkimage only: reads `<keydir>/<keyname>.bin`, hashes the regions, signs the
hash with `crypto_sign_signature`, and **zeroizes the secret key** before returning.

Verbatim, from `lib/ml-dsa/mldsa-sign.c`:

```c
int mldsa_sign(struct image_sign_info *info,
	       const struct image_region region[],
	       int region_count, uint8_t **sigp, uint *sig_len)
{
	uint8_t sk[MLDSA44_SECRETKEYBYTES];
	uint8_t *sig;
	uint8_t hash[info->checksum->checksum_len];
	size_t sig_len_sz = MLDSA44_BYTES;
	char path[1024];
	int ret;

	/* Build path to private key file */
	if (info->keyfile) {
		snprintf(path, sizeof(path), "%s", info->keyfile);
	} else if (info->keydir && info->keyname) {
		snprintf(path, sizeof(path), "%s/%s.bin",
			 info->keydir, info->keyname);
	} else {
		fprintf(stderr, "ML-DSA: no key specified\n");
		return -1;
	}

	/* Read private key */
	ret = mldsa_read_key(path, sk, MLDSA44_SECRETKEYBYTES);
	if (ret)
		goto err_sk;

	/* Hash the image regions */
	ret = info->checksum->calculate(info->checksum->name,
					region, region_count, hash);
	if (ret < 0) {
		fprintf(stderr, "ML-DSA: checksum calculation failed\n");
		goto err_sk;
	}

	/* Allocate signature buffer */
	sig = malloc(MLDSA44_BYTES);
	if (!sig) {
		fprintf(stderr, "ML-DSA: out of memory\n");
		ret = -ENOMEM;
		goto err_sk;
	}

	/* Sign the hash */
	ret = crypto_sign_signature(sig, &sig_len_sz,
				    hash, info->checksum->checksum_len,
				    NULL, 0, sk);
	if (ret) {
		fprintf(stderr, "ML-DSA: signing failed\n");
		free(sig);
		goto err_sk;
	}

	*sigp = sig;
	*sig_len = (uint)sig_len_sz;
	ret = 0;

err_sk:
	/* Always zero out secret key from memory */
	memset(sk, 0, sizeof(sk));
	return ret;
}
```

`mldsa_sign.c` also defines `randombytes()` (reads `/dev/urandom`) and a static
`mldsa_read_key()` helper (opens the key file, reads `expected_len` bytes, errors on
size mismatch). Both are host-only and live in the same file.

---

## 7. Vendoring layout (`lib/ml-dsa/`)

The mldsa-native library is vendored into U-Boot at `lib/ml-dsa/`. Upstream source =
`~/mldsa-native/mldsa/` (the `mldsa/` subdirectory of the pq-code-package repo).

| File | Role |
|------|------|
| `mldsa-verify.c` | U-Boot wrapper: `mldsa_verify` + `U_BOOT_CRYPTO_ALGO(mldsa44)` (target) |
| `mldsa-sign.c` | host wrapper: `mldsa_sign`, `mldsa_add_verify_data`, `randombytes`, `mldsa_read_key` |
| `mldsa_native.c` | the mldsa-native implementation (single compiled unit) |
| `mldsa_native.h` | upstream public API header (sizes, `crypto_sign_*`) |
| `mldsa_native_config.h` | config header with `MLD_CONFIG_PARAMETER_SET=44` |
| `src/` (+ `src/fips202/`) | the full mldsa-native core (`sign.c`, `poly*.c`, `packing.c`, `ct.c`, fips202) |
| `assert.h` | small assert shim for the vendored code |
| `Kconfig`, `Makefile` | build wiring (see §8) |
| `include/u-boot/ml-dsa.h` | **new** public header (only `MLDSA44_PUBLICKEYBYTES`=1312 and `MLDSA44_BYTES`=2420, + 3 prototypes) — lives under `include/`, not `lib/ml-dsa/` |

> **Note (intentional):** `include/u-boot/ml-dsa.h` deliberately does **not** define
> `MLDSA44_SECRETKEYBYTES`. The target verify path only needs the public-key and
> signature sizes. The host signer (`mldsa-sign.c`) gets `MLDSA44_SECRETKEYBYTES`
> from the vendored `mldsa_native.h` (which it `#include`s directly), so this is not
> a missing define.

The build uses the **portable C backend**; the optional native arithmetic backends
(`mldsa/src/native/aarch64`, `.../x86_64`) are omitted (native-backend arith disabled).

Verbatim, `include/u-boot/ml-dsa.h`:

```c
/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * ML-DSA (FIPS 204) signature support for U-Boot FIT image verification
 */

#ifndef _ML_DSA_H
#define _ML_DSA_H

#include <errno.h>
#include <image.h>

/* ML-DSA-44 sizes */
#define MLDSA44_PUBLICKEYBYTES	1312
#define MLDSA44_BYTES		2420

int mldsa_sign(struct image_sign_info *info,
	       const struct image_region region[],
	       int region_count, uint8_t **sigp, uint *sig_len);

int mldsa_add_verify_data(struct image_sign_info *info, void *keydest);

int mldsa_verify(struct image_sign_info *info,
		 const struct image_region region[], int region_count,
		 uint8_t *sig, uint sig_len);

#endif /* _ML_DSA_H */
```

---

## 8. Build wiring

> ⚠️ **Line numbers below are PoC-tree references ONLY.** They come from the PoC
> `~/u-boot` tree, which is **not** byte-identical to the clean `v2026.04` baseline.
> Confirmed discrepancy: the clean tree's `boot/image-sig.c` has
> `#include <asm/global_data.h>` (line 8) that the PoC lacks, so PoC line numbers are
> already offset. **`generate_patch` and the patch must locate every insertion point
> by surrounding content / anchor lines — never by line number.** Anchor context is
> quoted for each site so the insertion is unambiguous.

### `lib/ml-dsa/Kconfig` (new file, verbatim)
```kconfig
# SPDX-License-Identifier: GPL-2.0+
config ML_DSA
	bool "Use ML-DSA Library"
	select ML_DSA_VERIFY
	help
	  ML-DSA (FIPS 204) support. This enables the ML-DSA-44 algorithm
	  used for FIT image verification in U-Boot, as a post-quantum
	  replacement for RSA.
	  See doc/usage/fit/signature.rst for more details.

config ML_DSA_VERIFY
	bool
	help
	  Add ML-DSA signature verification support.
```

### `lib/Kconfig` — add a `source` line
Anchor (insert the `ml-dsa` line between the `rsa` and `crypto` sources):
```kconfig
source "lib/ecdsa/Kconfig"
source "lib/rsa/Kconfig"
source "lib/ml-dsa/Kconfig"        <-- ADDED
source "lib/crypto/Kconfig"
source "lib/crypt/Kconfig"
```

### `lib/Makefile` — descend into `ml-dsa/`
Anchor (insert after the RSA line):
```make
obj-$(CONFIG_ECDSA) += ecdsa/
obj-$(CONFIG_$(PHASE_)RSA) += rsa/
obj-$(CONFIG_ML_DSA) += ml-dsa/        <-- ADDED
obj-$(CONFIG_HASH) += hash-checksum.o
```

### `lib/ml-dsa/Makefile` (new file, verbatim)
```make
# SPDX-License-Identifier: GPL-2.0+
ccflags-y += -I$(srctree)/lib/ml-dsa

obj-$(CONFIG_ML_DSA_VERIFY) += mldsa-verify.o
obj-$(CONFIG_ML_DSA_VERIFY) += mldsa_native.o
```

### `tools/Makefile` — host (mkimage) build of the ML-DSA objects
Define the host object set (anchor: right after the `ECDSA_OBJS-...` line) and its
per-object include flags:
```make
ECDSA_OBJS-$(CONFIG_TOOLS_LIBCRYPTO) := $(addprefix generated/lib/ecdsa/, ecdsa-libcrypto.o)
MLDSA_OBJS-$(CONFIG_FIT_SIGNATURE) := $(addprefix generated/lib/ml-dsa/, \
					mldsa-sign.o mldsa-verify.o \
					mldsa_native.o)                              <-- ADDED block
HOSTCFLAGS_generated/lib/ecdsa/ecdsa-libcrypto.o += \
	$(shell pkg-config --cflags libssl libcrypto 2> /dev/null || echo "")
HOSTCFLAGS_generated/lib/ml-dsa/mldsa_native.o += -I$(srctree)/lib/ml-dsa    <-- ADDED
HOSTCFLAGS_generated/lib/ml-dsa/mldsa-sign.o += -I$(srctree)/lib/ml-dsa      <-- ADDED
HOSTCFLAGS_generated/lib/ml-dsa/mldsa-verify.o += -I$(srctree)/lib/ml-dsa    <-- ADDED
```
And link the objects into the mkimage/dumpimage object list (anchor: in the
`$(ECDSA_OBJS-y) $(RSA_OBJS-y) ... $(AES_OBJS-y)` block):
```make
			$(ECDSA_OBJS-y) \
			$(RSA_OBJS-y) \
				$(MLDSA_OBJS-y) \        <-- ADDED
			$(AES_OBJS-y)
```

### `configs/rpi_arm64_defconfig` — enable verified boot (more than just ML-DSA!)

> **Verified by diffing the clean `v2026.04` rpi_arm64_defconfig against the PoC's.**
> The clean defconfig is a strict subset of the PoC's. Enabling ML-DSA alone is **not**
> enough — the FIT-signature verify path, control DTB, legacy image format (for
> `boot.scr`), and EFI disable are all required for the migration to actually boot.

Two parts:

**(a) Flip these EFI options IN PLACE** (the clean defconfig enables them near the
top; leaving them on risks the EFI Synchronous Abort and conflicts with disabling
EFI). Change:
```
CONFIG_EFI_RUNTIME_UPDATE_CAPSULE=y      ->   # CONFIG_EFI_RUNTIME_UPDATE_CAPSULE is not set
CONFIG_EFI_CAPSULE_FIRMWARE_RAW=y        ->   # CONFIG_EFI_CAPSULE_FIRMWARE_RAW is not set
```

**(b) Append the missing options** (clean lacks all of these; `CONFIG_EFI_LOADER` is
on-by-default with no explicit line, so it is disabled via an appended `is not set`):
```
CONFIG_FIT=y
CONFIG_FIT_SIGNATURE=y
CONFIG_OF_CONTROL=y
CONFIG_OF_SEPARATE=y
CONFIG_ML_DSA=y
CONFIG_ML_DSA_VERIFY=y
CONFIG_BOOTDELAY=-2
# CONFIG_EFI_LOADER is not set
CONFIG_LEGACY_IMAGE_FORMAT=y
```

Why each matters:
- **`CONFIG_FIT_SIGNATURE=y`** is the linchpin: it enables the verify path *and* gates
  `MLDSA_OBJS` in `tools/Makefile` — without it the host `mkimage` never builds/links
  the ML-DSA signer. `CONFIG_FIT=y` enables FIT images.
- **`CONFIG_ML_DSA=y`** selects `CONFIG_ML_DSA_VERIFY` (per the Kconfig above).
- **`CONFIG_OF_CONTROL` / `CONFIG_OF_SEPARATE`** — control-DTB path (the runtime
  pubkey injection in §10 overwrites `fdtcontroladdr`).
- **`CONFIG_LEGACY_IMAGE_FORMAT=y`** — so the boot script (`boot.scr`) is accepted.
- **`# CONFIG_EFI_LOADER is not set`** (+ the two capsule flips) — disable EFI to
  avoid the Synchronous Abort. With `EFI_LOADER` off, kconfig drops the dependent
  capsule options entirely.

> **PoC quirk (do NOT replicate):** the PoC defconfig *appends* the EFI `is not set`
> lines at the bottom (relying on kconfig last-wins to override the top-of-file `=y`)
> and even duplicates the three EFI lines. The clean patch flips them in place and
> appends each option once — same effective config, no contradiction.

**Verified:** after `make rpi_arm64_defconfig`, the generated `.config` contains
`CONFIG_FIT_SIGNATURE=y`, `CONFIG_ML_DSA_VERIFY=y`, `CONFIG_LEGACY_IMAGE_FORMAT=y`,
`CONFIG_OF_CONTROL=y`, and `# CONFIG_EFI_LOADER is not set`; `make tools` then links
`mldsa-sign.o`/`mldsa-verify.o`/`mldsa_native.o` into `mkimage`.

---

## 9. Build-time public-key embed (delivery mechanism #1)

The standard U-Boot signed-FIT flow, two passes:

1. **Pass 1** builds the control DTB.
2. `mkimage` (with the host `mldsa44` algo from §3/§4) runs `mldsa_add_verify_data`
   to insert `mldsa,public-key` into the control DTB's `/signature/key-<name>` node.
3. **Pass 2** re-embeds that pubkey-bearing DTB into the U-Boot binary.

This is the portable/expected path. On the Pi 5 it is **not** what actually governs
verification — see §10.

---

## 10. Runtime public-key injection (delivery mechanism #2 — governs verification on Pi 5)

The Pi 5 firmware hands U-Boot its **own** DTB, overriding the built-in control DTB
from §9. So the boot script re-injects the pubkey DTB over `fdtcontroladdr` before
`bootm`. **This is the key that verification actually uses on the Pi 5.**

Target `boot.txt` (renamed from the PoC — see §11):

```
cp.b ${fdtcontroladdr} ${fdt_addr_r} 0x20000
load mmc 0:1 0x10000000 u-boot.dtb
cp.b 0x10000000 ${fdtcontroladdr} 0xba98
load mmc 0:1 0x30000000 rpi5.itb
setenv bootargs "console=ttyAMA10,115200 console=tty1 root=PARTUUID=29e21ec0-02 rootfstype=ext4 fsck.repair=yes rootwait pqc-boot_verified=1"
if bootm start 0x30000000#conf-1; then unzip 0x300000f0 0x80000; load mmc 0:1 0x10000000 initramfs_2712; booti 0x80000 0x10000000:${filesize} ${fdt_addr_r}; fi
echo "pqc-boot: ML-DSA-44 verification failed, refusing to boot"
```

Line-by-line:
1. Copy the firmware-supplied control DTB to `fdt_addr_r` (working copy for the kernel).
2. Load the pubkey-embedded `u-boot.dtb` to `0x10000000`.
3. **Overwrite `fdtcontroladdr` with the pubkey DTB** — this is the runtime injection.
4. Load the FIT image `rpi5.itb` to `0x30000000`.
5. Set `bootargs`, baking in the verified marker `pqc-boot_verified=1`.
6. `bootm start …#conf-1` runs verification; on success, unzip the kernel, load the
   initramfs, and `booti`.
7. On verification failure, echo the failure message and refuse to boot.

> ⚠️ **PoC-SPECIFIC DERIVED values — recompute per build, NEVER hardcode.**
> - **`0xba98`** (line 3) = the byte size of the PoC `u-boot.dtb` (the pubkey-embedded
>   control DTB), used as the `cp.b` **copy length**. If the rebuilt DTB has a
>   different size, a hardcoded `0xba98` **truncates the DTB and verification fails**.
>   The boot-script generator must compute this from the actual built DTB size
>   (e.g. the on-disk size of `u-boot.dtb`).
> - **`0x300000f0`** (line 6, `unzip`) = the offset of the FIT image *data* within
>   `rpi5.itb` as loaded at `0x30000000`. This is FIT-layout-dependent and must be
>   recomputed per build, not embedded as a literal.
>
> Other addresses (`0x20000`, `0x10000000`, `0x30000000`, `0x80000`) are fixed
> load/scratch addresses for this Pi 5 layout.

---

## 11. Branding rename map

The PoC tree used `quboot` branding. When reconstructing the patch and generating
the boot script, rename **only** these user-facing strings:

| PoC token | Target token | Where |
|-----------|--------------|-------|
| `quboot_verified=1` | `pqc-boot_verified=1` | boot.txt `bootargs` (the verified marker) |
| `QUBOOT: ML-DSA-44 verification failed, refusing to boot` | `pqc-boot: ML-DSA-44 verification failed, refusing to boot` | boot.txt failure `echo` |

**Do NOT rename** code identifiers and DT property names — these are not branding:
`mldsa,public-key`, `mldsa44`, `CONFIG_ML_DSA` / `CONFIG_ML_DSA_VERIFY`,
`lib/ml-dsa/`, `MLDSA44_*`, `mldsa_verify` / `mldsa_sign` / `mldsa_add_verify_data`.

> The vendored `lib/ml-dsa/` sources and `include/u-boot/ml-dsa.h` were checked and
> contain **no** `quboot` tokens — the rebrand is confined to the boot script.

**Marker consistency:** the boot.txt marker must be character-identical to
`VERIFIED_MARKER` in [pqc_boot/config.py](../pqc_boot/config.py)
(`pqc-boot_verified=1`); the verify stage asserts this token (see
[tests/test_verify.py](../tests/test_verify.py)).

---

## 12. Patch inventory (the checklist for `patches/uboot-2026.04-mldsa44.diff`)

**Files added:**
- `lib/ml-dsa/mldsa-verify.c`
- `lib/ml-dsa/mldsa-sign.c`
- `lib/ml-dsa/mldsa_native.c`
- `lib/ml-dsa/mldsa_native.h`
- `lib/ml-dsa/mldsa_native_config.h` (param set 44)
- `lib/ml-dsa/src/**` (+ `src/fips202/**`)
- `lib/ml-dsa/assert.h`
- `lib/ml-dsa/Kconfig`
- `lib/ml-dsa/Makefile`
- `include/u-boot/ml-dsa.h`

**Files modified:**
- `boot/image-sig.c` — add `#include <u-boot/ml-dsa.h>` (no table edit; §3)
- `tools/image-sig-host.c` — add `#include <u-boot/ml-dsa.h>` + `mldsa44` entry in `crypto_algos[]` (§3)
- `lib/Kconfig` — add `source "lib/ml-dsa/Kconfig"` (§8)
- `lib/Makefile` — add `obj-$(CONFIG_ML_DSA) += ml-dsa/` (§8)
- `tools/Makefile` — `MLDSA_OBJS`, host CFLAGS, link into mkimage (§8)
- `configs/rpi_arm64_defconfig` — the **full §8 defconfig block**: 2 in-place EFI
  flips (capsule options → `is not set`) + 9 appended options (`CONFIG_FIT`,
  `CONFIG_FIT_SIGNATURE`, `CONFIG_OF_CONTROL`, `CONFIG_OF_SEPARATE`, `CONFIG_ML_DSA`,
  `CONFIG_ML_DSA_VERIFY`, `CONFIG_BOOTDELAY=-2`, `# CONFIG_EFI_LOADER is not set`,
  `CONFIG_LEGACY_IMAGE_FORMAT`). **Not** just the two ML_DSA lines — see §8.

The reconstructed patch must hit exactly these sites (8 modified/added groups), and
must rename per §11. The boot script (§10) is generated separately by the tool, not
part of the U-Boot patch.
