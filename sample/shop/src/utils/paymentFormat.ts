import { formatMoney } from './currency'

// Text shown on the payment summary. Payment wording only; no payment logic here.
export function paymentLabel(order: { total: number }) {
  return 'Payment due: ' + formatMoney(order.total)
}

export function paymentStatusText(status: string) {
  return status === 'paid' ? 'Payment received' : 'Payment pending'
}
