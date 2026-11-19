const rows: Record<string, unknown>[] = []

export function insertRow(table: string, row: Record<string, unknown>) {
  rows.push({ table, ...row })
}

export function selectRows(table: string) {
  return rows.filter((r) => r.table === table)
}
