from __future__ import annotations


def compute_confidence_score(
    *,
    document_authentic: bool,
    face_matched: bool,
    liveness_passed: bool,
    name_match: bool,
    dob_match: bool,
    id_number_valid: bool,
    iprs_match: bool,
    duplicate_detected: bool,
    pep_flag: bool,
    blacklist_flag: bool,
    sanctions_flag: bool,
    quality_front_passed: bool,
    quality_back_passed: bool,
    face_match_score: int,
) -> int:
    score = 0
    score += 18 if document_authentic else 0
    score += 18 if face_matched else 0
    score += 15 if liveness_passed else 0
    score += 10 if name_match else 0
    score += 8 if dob_match else 0
    score += 8 if id_number_valid else 0
    score += 8 if iprs_match else 0
    score += 6 if quality_front_passed else 0
    score += 4 if quality_back_passed else 0
    score += min(max(face_match_score, 0), 100) // 10

    if duplicate_detected:
        score -= 40
    if pep_flag:
        score -= 35
    if blacklist_flag:
        score -= 50
    if sanctions_flag:
        score = 0

    return max(0, min(100, score))
