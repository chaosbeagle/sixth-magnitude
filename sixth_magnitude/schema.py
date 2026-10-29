"""One dictionary drives the JSON Schema, browser form and tabular exports."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

VERSION = "0.2.0"
PROFILE = Path(__file__).resolve().parent / "profiles/lung_vocabulary.json"
VOCAB = json.loads(PROFILE.read_text(encoding="utf-8"))["vocabularies"]
LABELS = {
    "suspected_present": "疑似腫瘤", "no_evidence_in_view": "本視野未見腫瘤證據",
    "indeterminate": "不確定", "not_assessed": "未評估", "unassessable": "無法判讀",
    "not_applicable": "不適用", "assessed": "已評估", "adequate": "足夠", "limited": "受限",
    "low": "低", "moderate": "中", "high": "高",
    "lepidic": "貼壁型", "acinar": "腺泡型", "papillary": "乳突型",
    "micropapillary": "微乳突型", "solid": "實性", "complex_glandular": "複雜腺體",
    "mucinous": "黏液性形態", "other": "其他",
    "alveolar_structure": "肺泡結構", "collapsed_alveoli_candidate": "疑似塌陷肺泡",
    "fibrous_stroma": "纖維間質", "inflammatory_aggregate": "炎症細胞聚集",
    "blood_vessel": "血管", "airway": "氣道", "smooth_muscle": "平滑肌",
    "nerve_candidate": "疑似神經", "lymphoid_tissue": "淋巴組織", "necrosis": "壞死", "mucin": "黏液",
    "along_wall": "沿壁生長", "gland_forming": "腺體形成", "cell_nests": "細胞巢",
    "cell_sheets": "細胞片", "fibrovascular_core_candidate": "疑似纖維血管軸",
    "cribriform_candidate": "疑似篩狀", "fused_glands_candidate": "疑似融合腺體",
    "intratumour": "腫瘤內", "tumour_nontumour_interface": "腫瘤／非腫瘤交界",
    "extratumour": "腫瘤外", "pattern_transition": "形態轉換", "epithelial_stromal": "上皮／間質",
    "tumour_nontumour": "腫瘤／非腫瘤", "vessel_stromal": "血管／間質",
    "airway_alveolar": "氣道／肺泡", "tissue_edge": "組織邊緣", "tissue_gap": "組織間隙",
    "blur": "模糊", "fold": "摺疊", "tear": "破裂", "crush": "擠壓", "occlusion": "遮蔽",
    "padding": "影像外填補", "dominant": "主要成分", "mixed": "混合分布",
    "peripheral_only": "僅邊緣可見", "similar": "局部與周圍相似", "different": "局部與周圍不同",
    "STAS": "STAS 候選", "LVI": "淋巴血管侵犯候選", "VPI": "臟層胸膜侵犯候選",
    "invasion_boundary": "侵犯邊界候選",
}


def enum(title, values, default="not_assessed"):
    return {"type": "string", "title": title, "enum": values, "default": default}


def choices(title, values):
    return {"type": "array", "title": title, "items": {"type": "string", "enum": values},
            "uniqueItems": True, "default": []}


def note(title="簡短可見依據／補充"):
    return {"type": ["string", "null"], "title": title, "maxLength": 1000, "default": None}


def obj(title, properties):
    return {"type": "object", "title": title, "properties": properties,
            "required": list(properties), "additionalProperties": False}


def scope(title):
    states = VOCAB["pattern_assessment_state"]
    return obj(title, {
        "tumour_evidence": enum("腫瘤證據", VOCAB["tumour_evidence"]),
        "tumour_distribution": enum("疑似腫瘤在本範圍的分布", ["not_assessed", "not_applicable", "dominant", "mixed", "peripheral_only", "indeterminate"]),
        "pattern_assessment_state": enum("生長型態評估", states),
        "patterns_present": choices("有支持的型態（候選；腺癌型態不適用時可留空）", VOCAB["patterns_present"]),
        "components_assessment_state": enum("組織成分評估", states),
        "components_present": choices("可見組織成分", VOCAB["components_present"]),
        "architecture_assessment_state": enum("可見結構評估", states),
        "architecture_evidence": choices("可見結構", VOCAB["architecture_evidence"]),
        "assessability": enum("可判讀程度", VOCAB["assessability"]),
        "uncertainty_level": enum("判讀不確定性", VOCAB["uncertainty_level"]),
        "note": note(),
    })


SCHEMA = obj("六等星肺部 WSI 多尺度觀察", {
    "schema_version": {"type": "string", "const": VERSION, "default": VERSION},
    "label": {"type": "string", "title": "標註名稱", "minLength": 1, "maxLength": 160, "default": ""},
    "focus": scope("局部形態"),
    "context": scope("周圍組織（排除局部範圍）"),
    "relationship": obj("空間關係", {
        "tumour_relative_location": enum("局部相對於腫瘤的位置", VOCAB["tumour_relative_location"]),
        "boundary_assessment_state": enum("交界評估", VOCAB["pattern_assessment_state"]),
        "boundary_types": choices("交界種類", VOCAB["boundary_type"]),
        "local_context_relation": enum("局部與周圍的觀察關係", ["not_assessed", "similar", "different", "indeterminate"]),
        "note": note(),
    }),
    "quality": obj("影像品質", {
        "assessment_state": enum("品質評估", VOCAB["pattern_assessment_state"]),
        "artifact_flags": choices("品質限制", VOCAB["artifact_flags"]), "note": note("限制的位置與影響"),
    }),
    "special": obj("特殊事件候選（需要時填寫）", {
        "assessment_state": enum("候選事件評估", VOCAB["pattern_assessment_state"]),
        "candidates": choices("事件候選", VOCAB["special_focus_candidates"]), "note": note(),
    }),
})
SCHEMA.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
               "$id": "urn:sixth-magnitude:lung-observation:0.2.0"})
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)


def defaults(schema=SCHEMA):
    if schema.get("type") == "object":
        return {k: defaults(v) for k, v in schema["properties"].items()}
    return deepcopy(schema.get("default"))


def validate(observation):
    errors = sorted(VALIDATOR.iter_errors(observation), key=lambda e: str(list(e.path)))
    if errors:
        e = errors[0]
        raise ValueError(f'{".".join(map(str, e.path))}: {e.message}')
    if not observation["label"].strip():
        raise ValueError("標註名稱不得空白")
    # These are state contradictions, not pathology confidence gates.
    for region in ("focus", "context"):
        values = observation[region]
        for state, array in [("pattern_assessment_state", "patterns_present"),
                             ("components_assessment_state", "components_present"),
                             ("architecture_assessment_state", "architecture_evidence")]:
            if values[state] in ("not_assessed", "unassessable", "not_applicable") and values[array]:
                raise ValueError(f"{region}.{array}: 有選項時請將評估狀態改為已評估或不確定")
        if values["tumour_evidence"] in ("no_evidence_in_view", "not_applicable", "not_assessed", "unassessable") and values["tumour_distribution"] in ("dominant", "mixed", "peripheral_only"):
            raise ValueError(f"{region}: 腫瘤證據與分布狀態矛盾")
    for group, state, array in [("quality", "assessment_state", "artifact_flags"),
                                ("special", "assessment_state", "candidates"),
                                ("relationship", "boundary_assessment_state", "boundary_types")]:
        if observation[group][state] in ("not_assessed", "unassessable", "not_applicable") and observation[group][array]:
            raise ValueError(f"{group}: 有選項時請更新評估狀態")
    return observation
