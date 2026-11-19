// The BFF's only way to reach the data layer.
export async function dalRequest(method: string, path: string, body?: unknown) {
  const res = await fetch('https://dal.example.test' + path, { method, body: JSON.stringify(body) })
  return res.json()
}
