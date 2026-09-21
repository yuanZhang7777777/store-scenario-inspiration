/** Stable selection identities, independent of punctuation in scene/product names. */
export interface Selection { scene_name: string; product_cn: string; main_sku: string }
export function selectionKey(scene: string, product: string, sku: string): string {
  return JSON.stringify([scene, product, sku]);
}
export function readSelection(key: string): Selection | null {
  try {
    const parts: unknown = JSON.parse(key);
    if (Array.isArray(parts) && parts.length === 3 && parts.every((item) => typeof item === "string")) {
      return { scene_name: parts[0], product_cn: parts[1], main_sku: parts[2] };
    }
    return null;
  } catch { /* Legacy values used a plain delimiter. */ }
  const old = key.split("::");
  if (old.length !== 3) return null; // Ambiguous old keys cannot be guessed safely.
  return { scene_name: old[0], product_cn: old[1], main_sku: old[2] };
}
export function normalizedSelections(raw: unknown): Set<string> {
  const selected = new Set<string>();
  if (!Array.isArray(raw)) return selected;
  for (const key of raw) {
    if (typeof key !== "string") continue;
    const value = readSelection(key);
    if (value && value.main_sku) selected.add(selectionKey(value.scene_name, value.product_cn, value.main_sku));
  }
  return selected;
}
