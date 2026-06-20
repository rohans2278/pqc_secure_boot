/*
 * ML-DSA-44 keypair generation tool (pqc-boot).
 *
 * Generates raw binary private and public key files for U-Boot FIT signing —
 * the same encoding the on-device mldsa-native verifier expects (no PEM/DER).
 *
 * Output:
 *   <keydir>/<keyname>.pub  - 1312 byte raw public key
 *   <keydir>/<keyname>.bin  - 2560 byte raw private key
 *
 * Usage: ./keygen <keydir> <keyname>
 *
 * Build (see pqc_boot/stages/keys.py): the vendored mldsa_native_config.h gates the
 * keypair/sign APIs behind USE_HOSTCC, so this MUST be compiled with -DUSE_HOSTCC:
 *   cc -O2 -DUSE_HOSTCC -DMLD_CONFIG_PARAMETER_SET=44 -I. keygen.c mldsa_native.c -o keygen
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "mldsa_native_config.h"
#include "mldsa_native.h"

/* Required by mldsa-native: it calls randombytes() to seed key generation. */
int randombytes(uint8_t *out, size_t outlen)
{
	FILE *f = fopen("/dev/urandom", "rb");
	if (!f) {
		fprintf(stderr, "keygen: cannot open /dev/urandom\n");
		return -1;
	}
	if (fread(out, 1, outlen, f) != outlen) {
		fclose(f);
		fprintf(stderr, "keygen: failed to read random bytes\n");
		return -1;
	}
	fclose(f);
	return 0;
}

static int write_key(const char *path, const uint8_t *data, size_t len)
{
	FILE *f = fopen(path, "wb");
	if (!f) {
		fprintf(stderr, "keygen: cannot create '%s'\n", path);
		return -1;
	}
	if (fwrite(data, 1, len, f) != len) {
		fprintf(stderr, "keygen: failed to write '%s'\n", path);
		fclose(f);
		return -1;
	}
	fclose(f);
	return 0;
}

int main(int argc, char *argv[])
{
	uint8_t pk[MLDSA44_PUBLICKEYBYTES];
	uint8_t sk[MLDSA44_SECRETKEYBYTES];
	char pub_path[1024];
	char priv_path[1024];
	int ret;

	if (argc != 3) {
		fprintf(stderr, "Usage: %s <keydir> <keyname>\n", argv[0]);
		return 1;
	}

	snprintf(pub_path, sizeof(pub_path), "%s/%s.pub", argv[1], argv[2]);
	snprintf(priv_path, sizeof(priv_path), "%s/%s.bin", argv[1], argv[2]);

	ret = crypto_sign_keypair(pk, sk);
	if (ret != 0) {
		fprintf(stderr, "keygen: keypair generation failed (%d)\n", ret);
		memset(sk, 0, sizeof(sk));
		return 1;
	}

	ret = write_key(pub_path, pk, MLDSA44_PUBLICKEYBYTES);
	if (ret) {
		memset(sk, 0, sizeof(sk));
		return 1;
	}

	ret = write_key(priv_path, sk, MLDSA44_SECRETKEYBYTES);
	memset(sk, 0, sizeof(sk));
	if (ret)
		return 1;

	printf("Public key:  %s (%d bytes)\n", pub_path, MLDSA44_PUBLICKEYBYTES);
	printf("Private key: %s (%d bytes)\n", priv_path, MLDSA44_SECRETKEYBYTES);
	return 0;
}
