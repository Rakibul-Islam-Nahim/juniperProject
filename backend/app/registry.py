"""Lab-type → golden-image mapping.

Each golden image contains exactly one fixed lab (one topology, baked into
lab_agent's own config at image-build time — see lab_agent/app/config.py).
This registry's only job is choosing which golden image to boot for a given
lab_type; it no longer knows anything about topology files or device lists.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class LabTypeConfig:
    name: str
    golden_image: str


REGISTRY: dict[str, LabTypeConfig] = {
    "router": LabTypeConfig(name="router", golden_image="router.qcow2"),
    "switch": LabTypeConfig(name="switch", golden_image="switch.qcow2"),
    "switch-topology": LabTypeConfig(name="switch-topology", golden_image="switchtopology.qcow2"),
    "router-topology": LabTypeConfig(name="router-topology", golden_image="routertopology.qcow2"),
}


def get(lab_type: str) -> LabTypeConfig:
    try:
        return REGISTRY[lab_type]
    except KeyError as e:
        raise KeyError(f"unknown lab_type {lab_type!r}; known: {sorted(REGISTRY)}") from e
