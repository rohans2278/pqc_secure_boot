// SPDX-License-Identifier: GPL-2.0+
/*
 * ML-DSA (FIPS 204) signing support for U-Boot FIT images (host/mkimage only)
 */

#ifdef USE_HOSTCC
#include "mkimage.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <image.h>
#include <u-boot/ml-dsa.h>
#include "mldsa_native.h"

int randombytes(uint8_t *out, size_t outlen)
{
	FILE *f = fopen("/dev/urandom", "rb");
	if (!f)
		return -1;
	if (fread(out, 1, outlen, f) != outlen) {
		fclose(f);
		return -1;
	}
	fclose(f);
	return 0;
}

static int mldsa_read_key(const char *path, uint8_t *buf, size_t expected_len)
{
	FILE *f;
	size_t n;

	f = fopen(path, "rb");
	if (!f) {
		fprintf(stderr, "ML-DSA: cannot open key file '%s'\n", path);
		return -1;
	}
	n = fread(buf, 1, expected_len, f);
	fclose(f);
	if (n != expected_len) {
		fprintf(stderr, "ML-DSA: key file '%s' wrong size "
			"(got %zu, expected %zu)\n", path, n, expected_len);
		return -1;
	}
	return 0;
}

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

#endif /* USE_HOSTCC */