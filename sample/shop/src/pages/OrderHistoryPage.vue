<template>
  <ul><li v-for="o in orders" :key="o.id">{{ o.id }} {{ formatMoney(o.total) }}</li></ul>
</template>

<script setup lang="ts">
import { useOrderStore } from '@/stores/orderStore'
import { formatMoney } from '@/utils/currency'

const store = useOrderStore()
const orders = store.loadHistory()
</script>
