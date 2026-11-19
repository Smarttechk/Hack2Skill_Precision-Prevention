// Colours for charts and the map, matching styles.css (palette B, soft ocean teal; charts cannot read CSS variables).
export const C = {
  ink: "#1f3a44", ink2: "#4a6670", muted: "#8aa1a8", line: "#e8f1f3", tint: "#e0eef1",
  primary: "#3e8a9a", primaryDark: "#2e6f7d", slate: "#6b8fb8", slateDark: "#4a6a93",
  urgent: "#d9826b", high: "#e0b25c", routine: "#6fb49a",
};
// this system against usual care, in every chart
export const NEW = C.primary;
export const OLD = "#b8c9ce";

// map: few open gaps (green) to many (coral), through honey
export function gapColour(rate) {
  const stops = [[111, 180, 154], [224, 178, 92], [217, 130, 107]];
  const x = Math.max(0, Math.min(1, rate)) * 2;
  const i = Math.min(1, Math.floor(x)), f = x - i;
  const [a, b] = [stops[i], stops[i + 1]];
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
}
