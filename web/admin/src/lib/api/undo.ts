"use client";

/**
 * useUndo — 마지막 변경(audit entry)에 대한 5분 윈도우 되돌리기.
 *
 * DESIGN.md §1 #6 "Reversibility by default" — 즉시 적용된 변경에는 5분 undo
 * 토스트가 따라붙고, 운영자가 윈도우 안에서 ``Undo`` 버튼을 누르면 백엔드의
 * ``POST /audit/{id}/undo``를 호출해 이전 상태로 복원한다.
 *
 * 본 훅은 단일 last-undo 슬롯을 유지한다:
 *   - ``register({ auditId, label })`` — 새 변경을 등록(이전 슬롯 만료/덮어쓰기).
 *   - ``undo()`` — 슬롯이 살아 있다면 백엔드에 undo 요청.
 *   - ``current`` — 슬롯 메타. 토스트가 카운트다운을 그릴 때 참고.
 *
 * 윈도우는 컴포넌트 언마운트와 무관하게 동작해야 하므로 모듈 레벨 store를
 * subscribe 패턴으로 노출한다 (React 외부 상태).
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { fetchAdmin } from "./client";
import { AdminApiError } from "./errors";
import type { UndoAuditResponse } from "./types";

/** undo 윈도우 — admin-requirements §1 / DESIGN.md §1 #6. */
export const UNDO_WINDOW_MS = 5 * 60 * 1000;

export interface UndoSlot {
  /** 백엔드 audit entry id. */
  auditId: string;
  /** 토스트에 표시할 사람용 라벨 — 예: "LLM provider 변경". */
  label: string;
  /** 등록 시각 (epoch ms). */
  registeredAt: number;
  /** 만료 시각 (epoch ms). */
  expiresAt: number;
}

type Listener = (slot: UndoSlot | null) => void;
const _listeners = new Set<Listener>();
let _slot: UndoSlot | null = null;
let _expiryTimer: ReturnType<typeof setTimeout> | null = null;

function _notify(): void {
  for (const l of _listeners) {
    try {
      l(_slot);
    } catch {
      /* ignore */
    }
  }
}

function _clear(): void {
  if (_expiryTimer) {
    clearTimeout(_expiryTimer);
    _expiryTimer = null;
  }
  _slot = null;
  _notify();
}

/**
 * 새 undo 가능한 변경을 등록한다 — mutation 성공 콜백에서 호출.
 */
export function registerUndo(
  auditId: string,
  label: string,
  windowMs: number = UNDO_WINDOW_MS,
): UndoSlot {
  if (_expiryTimer) clearTimeout(_expiryTimer);
  const now = Date.now();
  _slot = {
    auditId,
    label,
    registeredAt: now,
    expiresAt: now + windowMs,
  };
  _expiryTimer = setTimeout(() => _clear(), windowMs);
  _notify();
  return _slot;
}

/** 외부에서 슬롯을 비울 때 — 예: undo 성공 후 또는 사용자 dismiss. */
export function consumeUndo(): void {
  _clear();
}

/** 현재 슬롯 — SSR/테스트에서 동기 접근용. */
export function getUndoSlot(): UndoSlot | null {
  return _slot;
}

export interface UseUndoResult {
  /** 현재 등록된 슬롯. 없으면 ``null``. */
  current: UndoSlot | null;
  /** 슬롯이 있다면 백엔드에 undo 요청을 보낸다. */
  undo: () => Promise<UndoAuditResponse | null>;
  /** 새 변경을 등록 — 일반적으로 mutation 콜백에서 호출. */
  register: (auditId: string, label: string, windowMs?: number) => UndoSlot;
  /** 진행 중 여부. */
  isUndoing: boolean;
  /** 마지막 undo 결과 에러 (성공이면 null). */
  error: AdminApiError | null;
}

/**
 * 5분 윈도우 undo 훅.
 *
 * 컴포넌트는 ``current``를 보고 토스트를 렌더링하고, ``undo()``를 호출해 백엔드에
 * 되돌리기를 요청한다. 슬롯은 모듈 레벨이라 화면 전환과 무관하게 유지된다.
 */
export function useUndo(): UseUndoResult {
  const [slot, setSlot] = useState<UndoSlot | null>(_slot);
  const [isUndoing, setIsUndoing] = useState(false);
  const [error, setError] = useState<AdminApiError | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    const listener: Listener = (s) => {
      if (mountedRef.current) setSlot(s);
    };
    _listeners.add(listener);
    // 마운트 시 현재 슬롯 동기화.
    setSlot(_slot);
    return () => {
      mountedRef.current = false;
      _listeners.delete(listener);
    };
  }, []);

  const undo = useCallback(async (): Promise<UndoAuditResponse | null> => {
    const target = _slot;
    if (!target) return null;
    setIsUndoing(true);
    setError(null);
    try {
      const result = await fetchAdmin<UndoAuditResponse>(
        `/audit/${encodeURIComponent(target.auditId)}/undo`,
        { method: "POST" },
      );
      _clear();
      return result;
    } catch (e) {
      const err =
        e instanceof AdminApiError
          ? e
          : new AdminApiError(String(e), 0, "unknown");
      setError(err);
      throw err;
    } finally {
      if (mountedRef.current) setIsUndoing(false);
    }
  }, []);

  return {
    current: slot,
    undo,
    register: registerUndo,
    isUndoing,
    error,
  };
}
