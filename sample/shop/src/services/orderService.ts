import { apiRequest } from '@/lib/apiClient'

export const orderService = {
  startPayment(orderId: string) {
    return apiRequest('POST', `/orders/${orderId}/payment`)
  },
  listOrders() {
    return apiRequest('GET', '/orders')
  },
}
