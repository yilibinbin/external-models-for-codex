export function gitCommandTimedOut(result) {
  return String(result?.errorCode ?? "") === "ETIMEDOUT";
}
