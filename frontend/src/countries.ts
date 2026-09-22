/**
 * The country a store sells into, by the name the operator uses for it.
 *
 * The rest of the app carries the two-letter code, because that is what the
 * catalogue and the stock workbook are keyed by. Screens and exports show the
 * name, because 「TH」 tells the person reading the sheet nothing about which
 * warehouse the goods are in.
 */
export const COUNTRY_NAMES: Record<string, string> = {
  PH: "菲律宾", TH: "泰国", VN: "越南", MY: "马来西亚",
};

export function countryName(code: string | null | undefined): string {
  const key = String(code ?? "").toUpperCase();
  return COUNTRY_NAMES[key] || key;
}
