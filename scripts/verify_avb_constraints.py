#!/usr/bin/env python3
"""Offline AVB signature/hash checks, including expected candidate rejection.

Does not change AVB flags, sign images, access a device or flash anything.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AVBTOOL_SHA256 = "e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b"


def main():
    tool = ROOT / "vendor/avbtool.py"
    if hashlib.sha256(tool.read_bytes()).hexdigest() != AVBTOOL_SHA256:
        raise ValueError("Unexpected avbtool source hash")
    spec = importlib.util.spec_from_file_location("avbtool", tool)
    avb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(avb)
    parser = avb.Avb()

    def metadata(name):
        image = avb.ImageHandler(str(ROOT / "backups" / (name + ".img")), read_only=True)
        footer, header, descriptors, _ = parser._parse_image(image)
        image.seek(footer.vbmeta_offset if footer else 0)
        blob = image.read(header.SIZE + header.authentication_data_block_size + header.auxiliary_data_block_size)
        signature_valid = avb.verify_vbmeta_signature(header, blob)
        if not signature_valid:
            raise ValueError("Embedded AVB signature failed: " + name)
        return header, descriptors, blob

    top, descriptors, root_blob = metadata("vbmeta_a")
    report = {"vbmeta_algorithm_type": top.algorithm_type, "vbmeta_flags": top.flags,
              "embedded_vbmeta_signature_valid": True,
              "hardware_trust_of_key_not_established": True, "stock_hashes": []}
    chain_blobs = []
    for desc in descriptors:
        if isinstance(desc, avb.AvbChainPartitionDescriptor):
            hdr, _, blob = metadata(desc.partition_name + "_a")
            offset = hdr.SIZE + hdr.authentication_data_block_size + hdr.public_key_offset
            if blob[offset:offset+hdr.public_key_size] != desc.public_key:
                raise ValueError("Chain key does not match: " + desc.partition_name)
            chain_blobs.append(blob)
        elif isinstance(desc, avb.AvbHashDescriptor):
            image = ROOT / "backups" / (desc.partition_name + "_a.img")
            with image.open("rb") as stream:
                data = stream.read(desc.image_size)
            if len(data) != desc.image_size:
                raise ValueError("Stock image shorter than AVB descriptor")
            computed = hashlib.new(desc.hash_algorithm, desc.salt + data).digest()
            matched = computed == desc.digest
            if not matched:
                raise ValueError("Stock image AVB hash mismatch: " + desc.partition_name)
            report["stock_hashes"].append({"partition": desc.partition_name,
                "covered_bytes": desc.image_size, "matched": True,
                "digest": desc.digest.hex()})
            if desc.partition_name == "boot":
                candidate = ROOT / "artifacts/boot_a.magisk-v31.1.candidate.img"
                with candidate.open("rb") as stream:
                    candidate_data = stream.read(desc.image_size)
                candidate_digest = hashlib.new(desc.hash_algorithm, desc.salt + candidate_data).digest()
                report["candidate_matches_signed_stock_boot_descriptor"] = candidate_digest == desc.digest
                report["candidate_digest_under_stock_descriptor"] = candidate_digest.hex()
    report["combined_vbmeta_sha256"] = hashlib.sha256(root_blob + b"".join(chain_blobs)).hexdigest()
    props = (ROOT / "evidence/getprop.txt").read_text()
    expected = "[ro.boot.vbmeta.digest]: [" + report["combined_vbmeta_sha256"] + "]"
    report["combined_vbmeta_digest_matches_running_device"] = expected in props
    if not report["combined_vbmeta_digest_matches_running_device"]:
        raise ValueError("AVB metadata digest differs from running device")
    report.update(safe_to_flash=False, recovery_verified=False,
                  result="Candidate is structurally patched; enforcing stock AVB rejects its changed boot hash.")
    output = ROOT / "evidence/avb-constraints.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
