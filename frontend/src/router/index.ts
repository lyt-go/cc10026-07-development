import { createRouter, createWebHistory } from 'vue-router'

import ArchiveMergeView from '@/views/ArchiveMergeView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径需排在动态 :moduleKey 之前，否则会被工作台通配规则抢先匹配
    { path: '/archive_merge', name: 'archive_merge', component: ArchiveMergeView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
