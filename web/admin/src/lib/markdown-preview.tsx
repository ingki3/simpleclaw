/**
 * 미니 마크다운 렌더러 — 외부 라이브러리 없이 페르소나 미리보기 우측 패널용.
 *
 * Tiptap 미채택(BIZ-38 결정)에 맞춰 react-markdown 류도 도입하지 않고,
 * 헤딩(#~####), 글머리 기호(- / *), 인라인 코드(`...`), 굵게(**...**),
 * 코드 펜스(```)만 다룬다. 정확한 마크다운 렌더가 필요하면 후속 이슈에서 교체.
 *
 * 안전: 모든 텍스트는 React가 자동 escape하므로 dangerouslySetInnerHTML을 쓰지 않는다.
 */

import { Fragment, type ReactNode } from "react";

interface Block {
  kind: "heading" | "paragraph" | "list" | "code" | "hr" | "blockquote";
  level?: number;
  text?: string;
  items?: string[];
  lang?: string;
}

function tokenize(md: string): Block[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // 코드 펜스
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++; // 닫는 ``` 스킵
      blocks.push({ kind: "code", text: buf.join("\n"), lang });
      continue;
    }

    // 가로 구분선
    if (/^---+\s*$/.test(line)) {
      blocks.push({ kind: "hr" });
      i++;
      continue;
    }

    // 헤딩
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ kind: "heading", level: h[1].length, text: h[2] });
      i++;
      continue;
    }

    // 인용
    if (line.startsWith("> ")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].startsWith("> ")) {
        buf.push(lines[i].slice(2));
        i++;
      }
      blocks.push({ kind: "blockquote", text: buf.join("\n") });
      continue;
    }

    // 리스트
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push({ kind: "list", items });
      continue;
    }

    // 빈 줄
    if (line.trim() === "") {
      i++;
      continue;
    }

    // 단락 — 다음 빈 줄이나 블록 시작까지 누적
    const buf: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,4}\s|---+\s*$|```|>\s|\s*[-*]\s)/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i++;
    }
    blocks.push({ kind: "paragraph", text: buf.join(" ") });
  }
  return blocks;
}

/**
 * 인라인 마크업: `**bold**`, `*italic*`, `` `code` `` 만 처리한다.
 * 정규식 기반 단순 파서 — 중첩은 지원하지 않는다.
 */
function renderInline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  // 토크나이즈: code(`...`), bold(**...**), italic(*...*) 순으로 우선순위
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      parts.push(
        <code
          key={`i${key++}`}
          className="rounded-[--radius-sm] bg-[--surface] px-1 py-0.5 font-mono text-[0.85em]"
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      parts.push(
        <strong key={`i${key++}`} className="font-semibold">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else {
      parts.push(<em key={`i${key++}`}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function MarkdownPreview({ source }: { source: string }) {
  const blocks = tokenize(source);
  return (
    <div className="space-y-3 text-sm text-[--foreground] leading-6">
      {blocks.map((b, idx) => {
        switch (b.kind) {
          case "heading": {
            const cls =
              b.level === 1
                ? "text-xl font-semibold text-[--foreground-strong]"
                : b.level === 2
                  ? "text-lg font-semibold text-[--foreground-strong]"
                  : b.level === 3
                    ? "text-base font-semibold text-[--foreground-strong]"
                    : "text-sm font-semibold text-[--foreground-strong]";
            return (
              <div key={idx} className={cls}>
                {renderInline(b.text ?? "")}
              </div>
            );
          }
          case "paragraph":
            return (
              <p key={idx} className="text-[--foreground]">
                {renderInline(b.text ?? "")}
              </p>
            );
          case "list":
            return (
              <ul
                key={idx}
                className="list-disc space-y-1 pl-5 text-[--foreground]"
              >
                {b.items?.map((it, j) => (
                  <li key={j}>{renderInline(it)}</li>
                ))}
              </ul>
            );
          case "code":
            return (
              <pre
                key={idx}
                className="overflow-x-auto rounded-[--radius-m] border border-[--border] bg-[--surface] p-3 font-mono text-xs"
              >
                <code>{b.text}</code>
              </pre>
            );
          case "blockquote":
            return (
              <blockquote
                key={idx}
                className="border-l-2 border-[--border-strong] pl-3 text-[--muted-foreground]"
              >
                {b.text?.split("\n").map((l, j) => (
                  <Fragment key={j}>
                    {renderInline(l)}
                    <br />
                  </Fragment>
                ))}
              </blockquote>
            );
          case "hr":
            return (
              <hr
                key={idx}
                className="my-4 border-0 border-t border-[--border]"
              />
            );
          default:
            return null;
        }
      })}
      {blocks.length === 0 ? (
        <p className="text-[--muted-foreground]">미리보기할 내용이 없습니다.</p>
      ) : null}
    </div>
  );
}
