import { startPayment, loadHistory } from './orderActions'

const state = { order: { id: 'o-1', total: 4200 }, status: 'idle' }

export function useOrderStore() {
  return {
    order: state.order,
    startPayment: (id: string) => startPayment(state, id),
    loadHistory: () => loadHistory(state),
  }
}
