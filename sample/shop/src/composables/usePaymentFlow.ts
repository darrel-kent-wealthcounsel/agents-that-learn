import { useOrderStore } from '@/stores/orderStore'

// Drives the checkout screen: holds the order and kicks off the charge.
export function usePaymentFlow() {
  const store = useOrderStore()
  const pay = () => store.startPayment(store.order.id)
  return { order: store.order, pay }
}
