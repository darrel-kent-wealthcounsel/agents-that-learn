const BASE = 'https://bff.example.test'

// One place for every browser call to the backend-for-frontend.
export async function apiRequest(method: string, path: string, body?: unknown) {
  const res = await fetch(BASE + path, { method, body: JSON.stringify(body) })
  return res.json()
}
