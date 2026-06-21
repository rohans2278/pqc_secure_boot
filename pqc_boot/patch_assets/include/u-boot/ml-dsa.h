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
