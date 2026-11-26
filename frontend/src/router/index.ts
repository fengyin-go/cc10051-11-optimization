import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
    {
      path: '/:moduleKey/continue/:entryId',
      name: 'module-continue',
      component: WorkbenchView,
      props: true,
    },
    {
      path: '/:moduleKey/detail/:entryId',
      name: 'module-detail',
      component: WorkbenchView,
      props: true,
    },
    {
      path: '/:moduleKey/apply/:applyId',
      name: 'module-apply',
      component: WorkbenchView,
      props: true,
    },
  ],
})

export default router
