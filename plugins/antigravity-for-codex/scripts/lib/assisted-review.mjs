export function nextAssistedReviewAction(state = {}) {
  const rounds = Array.isArray(state.rounds) ? state.rounds : [];
  const latest = rounds.at(-1);
  if (latest?.verdict === "approve" && latest.blockingFindings === 0 && latest.scoreTotal >= latest.threshold) {
    return { action: "stop", reason: "threshold_met" };
  }
  if (state.repeatedBlockingFinding) {
    return { action: "stop", reason: "repeated_blocker" };
  }
  if (rounds.length >= 2) {
    const [prev, current] = rounds.slice(-2);
    if (current.failureCategory) {
      return { action: "stop", reason: current.failureCategory };
    }
    if (
      Number.isFinite(current.scoreTotal)
      && Number.isFinite(prev.scoreTotal)
      && Number.isFinite(current.blockingFindings)
      && Number.isFinite(prev.blockingFindings)
      && current.scoreTotal <= prev.scoreTotal
      && current.blockingFindings >= prev.blockingFindings
    ) {
      return { action: "stop", reason: "no_improvement" };
    }
  }
  const maxRounds = Number.isInteger(state.maxRounds) ? state.maxRounds : 2;
  if (rounds.length >= maxRounds) {
    return { action: "stop", reason: "max_rounds" };
  }
  return { action: "review" };
}
