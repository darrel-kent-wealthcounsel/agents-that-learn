import { orderService } from '@/services'

// Marks the order as paying and asks the service to charge it.
export async function startPayment(state: any, orderId: string) {
  state.status = 'paying'
  const result = await orderService.startPayment(orderId)
  state.status = result.ok ? 'paid' : 'failed'
  return result
}

export function loadHistory(state: any) {
  return orderService.listOrders()
}
