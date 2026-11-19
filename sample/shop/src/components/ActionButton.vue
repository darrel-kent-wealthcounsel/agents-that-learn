<template>
  <button class="action" @click="$emit('click')">{{ label }}</button>
</template>

<script setup lang="ts">
defineProps<{ label: string }>()
defineEmits(['click'])
</script>
