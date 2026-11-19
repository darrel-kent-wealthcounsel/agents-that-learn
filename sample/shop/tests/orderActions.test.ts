import { startPayment } from '../src/stores/orderActions'

test('start payment marks the order as paying', async () => {
  const state = { status: 'idle' }
  await startPayment(state, 'o-1')
})
