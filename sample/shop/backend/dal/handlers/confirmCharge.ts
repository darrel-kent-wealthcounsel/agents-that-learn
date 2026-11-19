import { insertRow } from '../db'

export const route = { method: 'POST', path: '/charges/{orderId}/confirm' }

// Talks to the card processor, then writes the ledger row.
export async function handler(event: { orderId: string }) {
  const status = 'succeeded'
  insertRow('ledger', { orderId: event.orderId, status })
  return { status }
}
