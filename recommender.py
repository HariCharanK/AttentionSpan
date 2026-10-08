"""Versioned semantic ranking for AttentionSpan."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
import sqlite3

import numpy as np

from semantic import RANKER_CONFIG

TOPIC_PRIORS = {
    "post-training": 2.0, "post training": 2.0, "reinforcement learning": 2.0,
    "rlhf": 2.0, "agentic": 1.2, "agent": 1.2, "human data": 1.8,
    "computer use": 1.5, "security": 1.1, "distributed system": 1.4,
    "database": 0.8, "infrastructure": 0.8, "philosophy": 0.5,
    "medicine": 0.4, "health": 0.4,
}
FRESH_HALF_LIFE_DAYS = RANKER_CONFIG["freshness_half_life_days"]
CLUSTER_THRESHOLD = RANKER_CONFIG["cluster_threshold"]
MAX_INTEREST_GROUPS = RANKER_CONFIG["max_interest_groups"]
NEGATIVE_PENALTY_MAX = RANKER_CONFIG["negative_penalty_max"]
LANES = ("match", "match", "fresh", "match", "explore",
         "fresh", "match", "match", "fresh", "match")


def load_representations(con: sqlite3.Connection, semantic_version: str) -> tuple[dict, dict]:
    row = con.execute("SELECT dimensions,datatype FROM semantic_versions WHERE id=?",
                      (semantic_version,)).fetchone()
    if not row:
        return {}, {}
    dimensions, datatype = row
    if datatype != "float32":
        raise ValueError(f"Unsupported semantic datatype: {datatype}")
    vectors, profiles = {}, {}
    for item in con.execute("""SELECT item_id,profile_embedding,profile_json
                               FROM semantic_items WHERE semantic_version=?""",
                            (semantic_version,)):
        try:
            vector = np.frombuffer(item["profile_embedding"], dtype="<f4").copy()
            if vector.size != dimensions:
                continue
            vector /= np.linalg.norm(vector) or 1
            vectors[item["item_id"]] = vector
            profiles[item["item_id"]] = json.loads(item["profile_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return vectors, profiles


def _centroid(member_ids: list[str], vectors: dict[str, np.ndarray]) -> np.ndarray:
    value = np.mean(np.stack([vectors[item_id] for item_id in member_ids]), axis=0)
    return value / (np.linalg.norm(value) or 1)


def interest_groups(votes: dict[str, int], vectors: dict[str, np.ndarray],
                    threshold: float = CLUSTER_THRESHOLD,
                    max_groups: int = MAX_INTEREST_GROUPS) -> list[dict]:
    """Agglomerative positive-interest clusters, recomputed after every vote."""
    liked = sorted(item_id for item_id, vote in votes.items()
                   if vote == 1 and item_id in vectors)
    groups = [{"members": [item_id], "vector": vectors[item_id].copy()} for item_id in liked]
    while len(groups) > 1:
        best_similarity, best_pair = -2.0, None
        for i, left in enumerate(groups):
            for j in range(i + 1, len(groups)):
                similarity = float(left["vector"] @ groups[j]["vector"])
                if similarity > best_similarity:
                    best_similarity, best_pair = similarity, (i, j)
        if best_pair is None or (best_similarity < threshold and len(groups) <= max_groups):
            break
        i, j = best_pair
        members = groups[i]["members"] + groups[j]["members"]
        merged = {"members": sorted(members), "vector": _centroid(members, vectors)}
        groups = [group for k, group in enumerate(groups) if k not in (i, j)] + [merged]
    groups.sort(key=lambda group: tuple(group["members"]))
    return groups


def _ages(items: list[dict]) -> np.ndarray:
    current = datetime.now(timezone.utc)
    result = []
    for item in items:
        try:
            published = datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            result.append(max(0, (current - published).total_seconds() / 86400))
        except (ValueError, TypeError):
            result.append(90.0)
    return np.asarray(result, dtype=np.float32)


def _profile_text(item: dict, profiles: dict[str, dict]) -> str:
    profile = profiles.get(item["id"], {})
    return " ".join((item["title"], item["summary"][:400], profile.get("gist", ""),
                     " ".join(profile.get("themes", [])), profile.get("technical_detail", ""))).lower()


def rank(items: list[dict], votes: dict[str, int], vectors: dict[str, np.ndarray],
         profiles: dict[str, dict], limit: int = 120,
         skips: dict[str, int] | None = None, decision_count: int = 0) -> list[dict]:
    eligible = [item for item in items
                if item["id"] not in votes and (skips or {}).get(item["id"], 0) <= decision_count]
    if not eligible:
        return []
    ages = _ages(eligible)
    freshness = np.exp(-math.log(2) * ages / FRESH_HALF_LIFE_DAYS)
    groups = interest_groups(votes, vectors)
    count = len(eligible)
    relevance = np.zeros(count, dtype=np.float32)
    negative = np.zeros(count, dtype=np.float32)
    present = [(i, vectors[item["id"]]) for i, item in enumerate(eligible) if item["id"] in vectors]
    present_indices = np.asarray([i for i, _ in present], dtype=np.int32)
    if present:
        matrix = np.stack([vector for _, vector in present])
        if groups:
            prototypes = np.stack([group["vector"] for group in groups])
            relevance[present_indices] = np.max(matrix @ prototypes.T, axis=1)
        passed = [vectors[item_id] for item_id, vote in votes.items()
                  if vote == -1 and item_id in vectors]
        if passed:
            negative[present_indices] = np.max(matrix @ np.stack(passed).T, axis=1)

    semantic = np.clip((relevance - 0.43) / 0.42, 0, 1)
    negative_threshold = np.maximum(0.62, relevance - 0.03)
    penalty = NEGATIVE_PENALTY_MAX * np.clip(
        (negative - negative_threshold) / 0.30, 0, 1)
    prior = np.asarray([
        min(1.0, sum(weight for phrase, weight in TOPIC_PRIORS.items()
                     if phrase in _profile_text(item, profiles)) / 4)
        for item in eligible
    ], dtype=np.float32)

    match_score = 0.60 * semantic + 0.20 * freshness + 0.20 * prior - penalty
    fresh_score = 0.20 * semantic + 0.60 * freshness + 0.20 * prior - penalty
    adjacency = np.clip(1 - np.abs(semantic - 0.35) / 0.35, 0, 1)
    explore_score = 0.40 * adjacency + 0.30 * freshness + 0.30 * prior - 0.5 * penalty
    lane_scores = {"match": match_score, "fresh": fresh_score, "explore": explore_score}

    selected: list[int] = []
    used = np.zeros(count, dtype=bool)
    selected_lanes: dict[int, str] = {}
    for turn in range(min(limit, count)):
        lane = LANES[(decision_count + turn) % len(LANES)]
        scores = lane_scores[lane].copy()
        scores[used] = -np.inf
        pick = int(np.argmax(scores))
        selected.append(pick)
        used[pick] = True
        selected_lanes[pick] = lane

    result = []
    for i in selected:
        lane = selected_lanes[i]
        score = float(match_score[i])
        reason = ("Similar to your interests" if lane == "match" and groups and semantic[i] > 0.1
                  else "Newly published" if lane == "fresh" else "Explore an adjacent idea")
        result.append({**eligible[i], "score": round(score, 3),
                       "match": max(35, min(99, round(40 + score * 59))),
                       "why": reason, "lane": lane})
    return result


def preference_network(items: list[dict], votes: dict[str, int], vectors: dict[str, np.ndarray],
                       profiles: dict[str, dict], ordered: list[dict]) -> dict:
    by_id = {item["id"]: item for item in items}
    liked = [item_id for item_id, value in votes.items() if value == 1 and item_id in vectors][-65:]
    passed = [item_id for item_id, value in votes.items() if value == -1 and item_id in vectors][-20:]
    ids = list(dict.fromkeys(liked + passed + [item["id"] for item in ordered[:45]
                                              if item["id"] in vectors]))
    groups = interest_groups(votes, vectors)
    if not groups and ids:
        groups = [{"vector": vectors[ids[0]], "members": []}]
    clusters = []
    for index, group in enumerate(groups):
        labels = [profiles.get(item_id, {}).get("cluster_label", "") for item_id in group["members"]]
        label = Counter(value for value in labels if value).most_common(1)
        clusters.append({"id": index, "label": label[0][0] if label else f"Interest {index + 1}",
                         "liked": len(group["members"])})
    nodes = []
    for item_id in ids:
        item = by_id[item_id]
        vector = vectors[item_id]
        cluster = int(np.argmax([float(vector @ group["vector"]) for group in groups])) if groups else 0
        nodes.append({"id": item_id, "title": item["title"], "source": item["source"],
                      "kind": item["kind"], "category": item["category"], "url": item["url"],
                      "vote": votes.get(item_id, 0), "saved": votes.get(item_id) == 1,
                      "cluster": cluster, "theme": profiles.get(item_id, {}).get("cluster_label", "")})
    edges, linked = [], set()
    if ids:
        matrix = np.stack([vectors[item_id] for item_id in ids])
        similarities = matrix @ matrix.T
        np.fill_diagonal(similarities, -1)
        for i, item_id in enumerate(ids):
            for j in np.argsort(similarities[i])[-3:]:
                similarity = float(similarities[i, j])
                pair = tuple(sorted((item_id, ids[j])))
                if similarity >= 0.58 and pair not in linked:
                    linked.add(pair)
                    edges.append({"source": pair[0], "target": pair[1],
                                  "weight": round(similarity, 3)})
    return {"nodes": nodes, "edges": edges, "clusters": clusters,
            "method": "Versioned semantic profile embeddings"}
