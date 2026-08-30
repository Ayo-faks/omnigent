import { useEffect, useState } from "react";
import { DPIA_CASE_CHANGED_EVENT, loadDpiaCase } from "@/lib/dpia/dpiaApi";
import type { DpiaCaseSnapshot } from "@/lib/dpia/types";

export const DPIA_CASE_IDS = ["student-success-alert"] as const;

function readDpiaCases(): DpiaCaseSnapshot[] {
  return DPIA_CASE_IDS.map((caseId) => loadDpiaCase(caseId).caseData);
}

export function useDpiaCases(): DpiaCaseSnapshot[] {
  const [cases, setCases] = useState(readDpiaCases);
  useEffect(() => {
    const refresh = () => setCases(readDpiaCases());
    const onStorage = (event: StorageEvent) => {
      if (event.key?.startsWith("omnigent:dpia-case:")) refresh();
    };
    window.addEventListener(DPIA_CASE_CHANGED_EVENT, refresh);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(DPIA_CASE_CHANGED_EVENT, refresh);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return cases;
}
