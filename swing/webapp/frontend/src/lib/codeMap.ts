// 把券商导出的裸代码 + 市场名 → Tushare ts_code 格式
// 600110 + 上海 → 600110.SH
// 2166   + 深圳 → 002166.SZ

export function toTsCode(rawCode: string, market: string): string {
  if (!rawCode) return '';
  const padded = rawCode.padStart(6, '0');

  if (market.includes('上海') || market.includes('沪')) return `${padded}.SH`;
  if (market.includes('深圳') || market.includes('深')) return `${padded}.SZ`;
  if (market.includes('北京') || market.includes('京')) return `${padded}.BJ`;

  // Fallback by code prefix
  const first = padded[0];
  if (first === '6' || first === '5' || first === '9') return `${padded}.SH`;
  if (first === '0' || first === '3' || first === '2') return `${padded}.SZ`;
  if (first === '4' || first === '8') return `${padded}.BJ`;
  return padded;
}

// 是否是逆回购 / 国债逆回购代码（不计入持仓视图，但仍然影响现金）
export function isReverseRepo(rawCode: string): boolean {
  const c = rawCode.padStart(6, '0');
  return c.startsWith('204') || c.startsWith('131');
}
