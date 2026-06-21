// SPDX-License-Identifier: GPL-2.0+
/*
 * ML-DSA (FIPS 204) signature verification for U-Boot FIT images
 */

#ifndef USE_HOSTCC
#include <fdtdec.h>
#include <log.h>
#include <malloc.h>
#include <asm/types.h>
#include <asm/byteorder.h>
#include <linux/errno.h>
#include <asm/unaligned.h>
#else
#include "fdt_host.h"
#include "mkimage.h"
#include <linux/kconfig.h>
#include <fdt_support.h>
#endif

#include <u-boot/ml-dsa.h>
#include "mldsa_native.h"

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

#ifndef USE_HOSTCC
U_BOOT_CRYPTO_ALGO(mldsa44) = {
	.name = "mldsa44",
	.key_len = MLDSA44_PUBLICKEYBYTES,
	.verify = mldsa_verify,
};
#endif
