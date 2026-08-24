import { useCallback, useState } from "react";
import {
  applyCorrectionProposal,
  answerStakeholderQuestion,
  bindDpiaCaseSession,
  editCorrectionProposal,
  loadDpiaCase,
  recordOfficerDecision,
  replayValidatedInvestigation,
  recordDpiaLiveRun,
  rejectCorrectionProposal,
  resetDpiaCase,
  saveDpiaCase,
  stageCorrectionProposal,
  updateDpiaIntake,
  type DpiaLoadResult,
} from "@/lib/dpia/dpiaApi";
import type {
  CorrectionProposal,
  DpiaCaseSnapshot,
  DpiaLiveRunState,
  OfficerDecision,
} from "@/lib/dpia/types";

export interface DpiaCaseController {
  caseData: DpiaCaseSnapshot;
  source: DpiaLoadResult["source"];
  recoveredInvalidState: boolean;
  bindSession: (sessionId: string, actor?: string) => DpiaCaseSnapshot;
  stageCorrection: (proposal: CorrectionProposal, source: "agent" | "manual") => DpiaCaseSnapshot;
  editCorrection: (proposalId: string, proposal: CorrectionProposal) => DpiaCaseSnapshot;
  applyCorrection: (proposal: CorrectionProposal, actor?: string) => DpiaCaseSnapshot;
  rejectCorrection: (proposalId: string, actor?: string) => DpiaCaseSnapshot;
  recordLiveRun: (liveRun: DpiaLiveRunState | undefined) => DpiaCaseSnapshot;
  updateIntake: (values: Record<string, string>, actor?: string) => DpiaCaseSnapshot;
  answerQuestion: (input: {
    questionId: string;
    response: string;
    answeredBy: string;
  }) => DpiaCaseSnapshot;
  replaySnapshot: () => DpiaCaseSnapshot;
  decide: (
    decision: Omit<OfficerDecision, "processingModelVersion" | "policyPackVersion">,
  ) => DpiaCaseSnapshot;
  reset: () => DpiaCaseSnapshot;
}

export function useDpiaCase(caseId: string): DpiaCaseController {
  const [loadResult, setLoadResult] = useState(() => loadDpiaCase(caseId));

  const commit = useCallback((next: DpiaCaseSnapshot) => {
    saveDpiaCase(next);
    setLoadResult({ caseData: next, source: "persisted", recoveredInvalidState: false });
    return next;
  }, []);

  const updateIntake = useCallback(
    (values: Record<string, string>, actor = "Alex Morgan") =>
      commit(updateDpiaIntake(loadResult.caseData, values, new Date().toISOString(), actor)),
    [commit, loadResult.caseData],
  );

  const bindSession = useCallback(
    (sessionId: string, actor = "Alex Morgan") =>
      commit(bindDpiaCaseSession(loadResult.caseData, sessionId, new Date().toISOString(), actor)),
    [commit, loadResult.caseData],
  );

  const stageCorrection = useCallback(
    (proposal: CorrectionProposal, source: "agent" | "manual") => {
      const next = stageCorrectionProposal(
        loadResult.caseData,
        proposal,
        source,
        new Date().toISOString(),
      );
      return next === loadResult.caseData ? next : commit(next);
    },
    [commit, loadResult.caseData],
  );

  const editCorrection = useCallback(
    (proposalId: string, proposal: CorrectionProposal) =>
      commit(editCorrectionProposal(loadResult.caseData, proposalId, proposal)),
    [commit, loadResult.caseData],
  );

  const applyCorrection = useCallback(
    (proposal: CorrectionProposal, actor = "Alex Morgan") =>
      commit(
        applyCorrectionProposal(loadResult.caseData, proposal, actor, new Date().toISOString()),
      ),
    [commit, loadResult.caseData],
  );

  const rejectCorrection = useCallback(
    (proposalId: string, actor = "Alex Morgan") =>
      commit(
        rejectCorrectionProposal(loadResult.caseData, proposalId, actor, new Date().toISOString()),
      ),
    [commit, loadResult.caseData],
  );

  const recordLiveRun = useCallback(
    (liveRun: DpiaLiveRunState | undefined) =>
      commit(recordDpiaLiveRun(loadResult.caseData, liveRun)),
    [commit, loadResult.caseData],
  );

  const answerQuestion = useCallback(
    (input: { questionId: string; response: string; answeredBy: string }) =>
      commit(
        answerStakeholderQuestion(loadResult.caseData, {
          ...input,
          answeredAt: new Date().toISOString(),
        }),
      ),
    [commit, loadResult.caseData],
  );

  const decide = useCallback(
    (decision: Omit<OfficerDecision, "processingModelVersion" | "policyPackVersion">) =>
      commit(
        recordOfficerDecision(loadResult.caseData, {
          ...decision,
          processingModelVersion: loadResult.caseData.processingModel.version,
          policyPackVersion: loadResult.caseData.policyPack.version,
        }),
      ),
    [commit, loadResult.caseData],
  );

  const replaySnapshot = useCallback(
    () => commit(replayValidatedInvestigation(loadResult.caseData, new Date().toISOString())),
    [commit, loadResult.caseData],
  );

  const reset = useCallback(() => {
    const next = resetDpiaCase(caseId);
    setLoadResult({ caseData: next, source: "seed", recoveredInvalidState: false });
    return next;
  }, [caseId]);

  return {
    caseData: loadResult.caseData,
    source: loadResult.source,
    recoveredInvalidState: loadResult.recoveredInvalidState,
    bindSession,
    stageCorrection,
    editCorrection,
    applyCorrection,
    rejectCorrection,
    recordLiveRun,
    updateIntake,
    answerQuestion,
    replaySnapshot,
    decide,
    reset,
  };
}
