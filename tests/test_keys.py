"""Tests for the keys stage.

The cc-gated tests actually compile the vendored mldsa-native and prove the
generated keypair works with the same verifier the Pi uses (sign + verify +
tamper-reject). They skip cleanly where no C compiler is available.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from pqc_boot.config import Config
from pqc_boot.context import Context
from pqc_boot.stages import keys

CC = shutil.which("cc") or shutil.which("gcc")
needs_cc = pytest.mark.skipif(CC is None, reason="C compiler required")

# Minimal sign+verify+tamper harness, compiled against the same vendored _mldsa.
_ROUNDTRIP_C = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "mldsa_native_config.h"
#include "mldsa_native.h"
int randombytes(uint8_t *o, size_t n){FILE*f=fopen("/dev/urandom","rb");if(!f)return -1;
 if(fread(o,1,n,f)!=n){fclose(f);return -1;}fclose(f);return 0;}
static int rd(const char*p,uint8_t*b,size_t n){FILE*f=fopen(p,"rb");if(!f)return -1;
 size_t r=fread(b,1,n,f);fclose(f);return r==n?0:-1;}
int main(int argc,char**argv){
 char a[1024],c[1024];uint8_t pk[MLDSA44_PUBLICKEYBYTES],sk[MLDSA44_SECRETKEYBYTES];
 uint8_t sig[MLDSA44_BYTES],m[64];size_t sl=0;int r;
 snprintf(a,sizeof a,"%s/%s.pub",argv[1],argv[2]);
 snprintf(c,sizeof c,"%s/%s.bin",argv[1],argv[2]);
 if(rd(a,pk,sizeof pk)||rd(c,sk,sizeof sk))return 2;
 for(int i=0;i<64;i++)m[i]=(uint8_t)i;
 r=crypto_sign_signature(sig,&sl,m,sizeof m,NULL,0,sk);if(r)return 3;
 if(sl!=MLDSA44_BYTES)return 4;
 r=crypto_sign_verify(sig,sl,m,sizeof m,NULL,0,pk);if(r)return 5;
 sig[0]^=1;r=crypto_sign_verify(sig,sl,m,sizeof m,NULL,0,pk);if(r==0)return 6;
 printf("ROUNDTRIP OK\n");return 0;}
"""


def _ctx(tmp_path) -> Context:
    return Context.create(Config(workspace=Path(tmp_path)))


def test_plan_mentions_sizes_and_keyname(tmp_path):
    p = keys.plan(_ctx(tmp_path))
    assert "1312" in p and "2560" in p and "mykey" in p


@needs_cc
def test_generate_sizes_perms_idempotent(tmp_path):
    ctx = _ctx(tmp_path)
    keys.run(ctx)
    pub, priv = ctx.keydir / "mykey.pub", ctx.keydir / "mykey.bin"
    assert pub.stat().st_size == 1312
    assert priv.stat().st_size == 2560
    assert (priv.stat().st_mode & 0o777) == 0o600
    assert (ctx.keydir.stat().st_mode & 0o777) == 0o700
    # Rerun must not clobber the existing private key.
    before = priv.read_bytes()
    keys.run(ctx)
    assert priv.read_bytes() == before


@needs_cc
def test_keypair_signs_and_verifies(tmp_path):
    ctx = _ctx(tmp_path)
    keys.run(ctx)
    src = Path(tmp_path) / "rt.c"
    src.write_text(_ROUNDTRIP_C)
    binp = Path(tmp_path) / "rt"
    subprocess.run(
        [CC, "-O2", "-DUSE_HOSTCC", "-DMLD_CONFIG_PARAMETER_SET=44",
         f"-I{keys._MLDSA_DIR}", str(src), str(keys._MLDSA_DIR / "mldsa_native.c"),
         "-o", str(binp)],
        check=True, capture_output=True, text=True,
    )
    r = subprocess.run([str(binp), str(ctx.keydir), "mykey"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ROUNDTRIP OK" in r.stdout


def test_vendored_mldsa_matches_proven_source():
    proven = Path.home() / "u-boot" / "lib" / "ml-dsa" / "mldsa_native.c"
    if not proven.exists():
        pytest.skip("proven source not present on this host")
    assert (keys._MLDSA_DIR / "mldsa_native.c").read_bytes() == proven.read_bytes()
