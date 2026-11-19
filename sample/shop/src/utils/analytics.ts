// Usage counters. Event names mention payment start, but nothing here moves money.
const events: string[] = []

export function track(name: 'payment_start' | 'payment_start_failed' | 'payment_done') {
  events.push(name)
}

export function paymentStartCount() {
  return events.filter((e) => e === 'payment_start').length
}
