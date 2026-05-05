/**
 * memory-server 단위 테스트 — 파서·교체·삭제·드리밍 시뮬레이션.
 *
 * 디스크 IO와 spawnSync는 호출하지 않는다 — 본 테스트는 순수 함수 동작만 검증한다.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ENTRY_TYPES,
  getDreamingState,
  normalizeName,
  parseActiveProjectLine,
  parseMemoryIndex,
  readActiveProjects,
  removeEntry,
  replaceEntry,
  triggerDreaming,
} from "../memory-server";

describe("parseMemoryIndex", () => {
  it("맨 앞 자유 텍스트 bullet도 root 섹션으로 잡는다", () => {
    const text = "- root1\n- root2\n";
    const entries = parseMemoryIndex(text);
    expect(entries).toHaveLength(2);
    expect(entries[0].id).toBe("0:0");
    expect(entries[0].section).toBe("(root)");
    expect(entries[1].id).toBe("0:1");
  });

  it("## 헤더가 등장하면 섹션 카운터가 1 증가한다", () => {
    const text = "# Memory\n\n## 2026-04-28\n- A\n- B\n\n## 2026-04-29\n- C\n";
    const entries = parseMemoryIndex(text);
    expect(entries.map((e) => e.id)).toEqual(["2:0", "2:1", "3:0"]);
    expect(entries[0].section).toBe("2026-04-28");
    expect(entries[2].section).toBe("2026-04-29");
  });

  it("[type] prefix를 분류로 추출하고 본문에서 제거한다", () => {
    const text = "## s\n- [user] 사용자는 한국어 존댓말을 선호한다\n- [Feedback] 테스트 mock 금지\n- 그냥 항목\n";
    const entries = parseMemoryIndex(text);
    expect(entries[0].type).toBe("user");
    expect(entries[0].text).toBe("사용자는 한국어 존댓말을 선호한다");
    expect(entries[1].type).toBe("feedback");
    expect(entries[2].type).toBeNull();
    expect(entries[2].text).toBe("그냥 항목");
  });

  it("4가지 type 토큰을 모두 인식한다", () => {
    expect(ENTRY_TYPES).toEqual(["user", "feedback", "project", "reference"]);
    const text = ENTRY_TYPES.map((t) => `- [${t}] x`).join("\n");
    const entries = parseMemoryIndex(text);
    expect(entries.map((e) => e.type)).toEqual(ENTRY_TYPES);
  });

  it("빈 본문은 빈 배열을 반환한다", () => {
    expect(parseMemoryIndex("")).toEqual([]);
    expect(parseMemoryIndex("\n\n")).toEqual([]);
  });
});

describe("replaceEntry", () => {
  const sample = "# Memory\n\n## 2026-04-28\n- A\n- B\n\n## 2026-04-29\n- C\n";

  it("매칭 라인의 본문만 교체하고 마커·들여쓰기는 보존한다", () => {
    const next = replaceEntry(sample, "2:1", "B*");
    expect(next).toContain("- A\n- B*\n");
    // 다른 섹션 항목은 변경 없음
    expect(next).toContain("- C");
  });

  it("매칭 안 되는 id는 null 반환", () => {
    expect(replaceEntry(sample, "9:9", "x")).toBeNull();
    expect(replaceEntry(sample, "garbage", "x")).toBeNull();
  });
});

describe("removeEntry", () => {
  const sample = "## s\n- A\n- B\n- C\n";

  it("매칭 라인을 삭제한다", () => {
    const next = removeEntry(sample, "1:1");
    expect(next).not.toBeNull();
    // - B가 사라졌는지 확인
    const entries = parseMemoryIndex(next!);
    expect(entries.map((e) => e.text)).toEqual(["A", "C"]);
  });

  it("매칭 없으면 null", () => {
    expect(removeEntry(sample, "9:9")).toBeNull();
  });
});

describe("normalizeName (Active Projects, BIZ-96)", () => {
  it("공백·구두점을 제거하고 영문은 소문자화한다", () => {
    expect(normalizeName("SimpleClaw")).toBe("simpleclaw");
    expect(normalizeName(" Simple-Claw ")).toBe("simpleclaw");
    expect(normalizeName("Simple_Claw / Admin")).toBe("simpleclawadmin");
  });

  it("한글 음절은 보존한다 — 한·영 혼용 표기를 별도 키로 두기 위함", () => {
    expect(normalizeName("심플클로우")).toBe("심플클로우");
    expect(normalizeName(" 심플 클로우 · admin ")).toBe("심플클로우admin");
  });

  it("빈 입력은 빈 문자열 — 호출자가 무시 가드", () => {
    expect(normalizeName("")).toBe("");
    expect(normalizeName("   ")).toBe("");
  });
});

describe("parseActiveProjectLine (Active Projects, BIZ-96)", () => {
  it("정상 JSON 한 줄에서 name·last_seen 을 추출한다", () => {
    const out = parseActiveProjectLine(
      JSON.stringify({
        name: "SimpleClaw",
        role: "솔로 빌더",
        recent_summary: "...",
        first_seen: "2026-04-28T00:00:00",
        last_seen: "2026-05-04T12:00:00",
      }),
    );
    expect(out).not.toBeNull();
    expect(out!.name).toBe("SimpleClaw");
    expect(out!.lastSeen.toISOString()).toBe(
      new Date("2026-05-04T12:00:00").toISOString(),
    );
  });

  it("name 또는 last_seen 누락 시 null", () => {
    expect(parseActiveProjectLine(JSON.stringify({ name: "x" }))).toBeNull();
    expect(
      parseActiveProjectLine(JSON.stringify({ last_seen: "2026-05-01" })),
    ).toBeNull();
  });

  it("JSON 파싱 실패 / 비객체 / 비문자열 last_seen 은 null", () => {
    expect(parseActiveProjectLine("not json")).toBeNull();
    expect(parseActiveProjectLine("[1,2,3]")).toBeNull();
    expect(
      parseActiveProjectLine(JSON.stringify({ name: "x", last_seen: 123 })),
    ).toBeNull();
  });

  it("파싱 불가능한 last_seen 문자열은 null (NaN 가드)", () => {
    expect(
      parseActiveProjectLine(
        JSON.stringify({ name: "x", last_seen: "garbage" }),
      ),
    ).toBeNull();
  });
});

describe("readActiveProjects (Active Projects, BIZ-96)", () => {
  // 각 테스트는 독립된 임시 디렉토리에서 sidecar 를 만들고 SIMPLECLAW_AGENT_DIR 로
  // ``agentDir()`` 를 가리키게 한다. 다른 테스트의 agentDir 환경에 영향 주지 않도록
  // afterEach 에서 복원.
  let tmp: string;
  let originalEnv: string | undefined;

  beforeEach(async () => {
    originalEnv = process.env.SIMPLECLAW_AGENT_DIR;
    tmp = await fs.mkdtemp(path.join(tmpdir(), "active-projects-test-"));
    process.env.SIMPLECLAW_AGENT_DIR = tmp;
  });

  afterEach(async () => {
    if (originalEnv === undefined) delete process.env.SIMPLECLAW_AGENT_DIR;
    else process.env.SIMPLECLAW_AGENT_DIR = originalEnv;
    await fs.rm(tmp, { recursive: true, force: true });
  });

  it("sidecar 파일이 없으면 빈 리스트 + 기본 gate_policy 를 반환한다", async () => {
    const res = await readActiveProjects();
    expect(res.active_projects).toEqual([]);
    expect(res.gate_policy.single_observation_block).toBe(true);
    expect(res.gate_policy.cluster_threshold).toBeGreaterThan(0);
  });

  it("최근 last_seen 항목은 score 가 1 에 가깝고 last_seen 내림차순으로 정렬된다", async () => {
    const now = new Date();
    const today = new Date(now);
    const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    await fs.writeFile(
      path.join(tmp, "active_projects.jsonl"),
      [
        JSON.stringify({
          name: "OlderProject",
          role: "x",
          recent_summary: "y",
          first_seen: yesterday.toISOString(),
          last_seen: yesterday.toISOString(),
        }),
        JSON.stringify({
          name: "SimpleClaw",
          role: "솔로 빌더",
          recent_summary: "BIZ-96 구현",
          first_seen: today.toISOString(),
          last_seen: today.toISOString(),
        }),
      ].join("\n"),
      "utf8",
    );
    const res = await readActiveProjects();
    expect(res.active_projects).toHaveLength(2);
    // 가장 최근(=오늘) 항목이 위. id 는 normalize_name(name).
    expect(res.active_projects[0].id).toBe("simpleclaw");
    expect(res.active_projects[0].title).toBe("SimpleClaw");
    expect(res.active_projects[0].score).toBeGreaterThan(0.95);
    expect(res.active_projects[0].managed).toBe(true);
    expect(res.active_projects[1].id).toBe("olderproject");
  });

  it("윈도우(7일) 밖 last_seen 항목은 제외된다", async () => {
    const old = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    const recent = new Date();
    await fs.writeFile(
      path.join(tmp, "active_projects.jsonl"),
      [
        JSON.stringify({
          name: "Stale",
          role: "x",
          recent_summary: "",
          first_seen: old.toISOString(),
          last_seen: old.toISOString(),
        }),
        JSON.stringify({
          name: "Recent",
          role: "y",
          recent_summary: "",
          first_seen: recent.toISOString(),
          last_seen: recent.toISOString(),
        }),
      ].join("\n"),
      "utf8",
    );
    const res = await readActiveProjects();
    expect(res.active_projects.map((p) => p.title)).toEqual(["Recent"]);
  });

  it("같은 정규형 키가 두 줄 등장하면 마지막 줄이 채택된다 (Python load 와 동치)", async () => {
    const now = new Date();
    await fs.writeFile(
      path.join(tmp, "active_projects.jsonl"),
      [
        JSON.stringify({
          name: "SimpleClaw",
          role: "old",
          recent_summary: "",
          first_seen: now.toISOString(),
          last_seen: now.toISOString(),
        }),
        JSON.stringify({
          name: "Simple-Claw",
          role: "new",
          recent_summary: "",
          first_seen: now.toISOString(),
          last_seen: now.toISOString(),
        }),
      ].join("\n"),
      "utf8",
    );
    const res = await readActiveProjects();
    // 정규형은 동일 ("simpleclaw"). title 은 마지막 줄 표기.
    expect(res.active_projects).toHaveLength(1);
    expect(res.active_projects[0].id).toBe("simpleclaw");
    expect(res.active_projects[0].title).toBe("Simple-Claw");
  });

  it("손상된 줄은 skip 하고 정상 줄만 살아남는다", async () => {
    const now = new Date();
    await fs.writeFile(
      path.join(tmp, "active_projects.jsonl"),
      [
        "not json",
        JSON.stringify({ name: "x" }), // last_seen 누락
        JSON.stringify({
          name: "Good",
          role: "",
          recent_summary: "",
          first_seen: now.toISOString(),
          last_seen: now.toISOString(),
        }),
      ].join("\n"),
      "utf8",
    );
    const res = await readActiveProjects();
    expect(res.active_projects.map((p) => p.title)).toEqual(["Good"]);
  });
});

describe("triggerDreaming", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    // 모듈 상태 리셋을 위해 충분히 진행
  });

  it("실행 중에 다시 트리거하면 busy", () => {
    const first = triggerDreaming();
    expect(first.ok).toBe(true);
    const second = triggerDreaming();
    expect(second.ok).toBe(false);
    expect(second.reason).toBe("busy");
    expect(getDreamingState().running).toBe(true);
    // 끝까지 실행
    vi.runAllTimers();
    expect(getDreamingState().running).toBe(false);
    expect(getDreamingState().lastOutcome).toBe("success");
  });
});
