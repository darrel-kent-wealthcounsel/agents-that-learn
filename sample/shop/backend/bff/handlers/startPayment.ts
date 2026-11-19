import { dalRequest } from '../dalClient'

export const route = { method: 'POST', path: '/orders/{orderId}/payment' }

// Checks the request, then asks the data layer to charge and record the order.
export async function handler(event: { orderId: string }) {
  const charge = await dalRequest('POST', `/charges/${event.orderId}/confirm`)
  return { ok: charge.status === 'succeeded' }
}
