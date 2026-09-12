import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useOrganizeStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['organize', jobId],
    queryFn: () => api.getOrganizeStatus(jobId as string),
    enabled: jobId !== null,
    // A stale `data.status === 'running'` from before a failed refetch stays
    // in the cache on error (React Query doesn't clear it), so this must
    // also check query.state.status — otherwise a job that vanishes
    // server-side (e.g. a dev-server restart wiping in-memory job state)
    // 404s on every retry forever instead of ever giving up.
    refetchInterval: (query) =>
      query.state.status === 'success' && query.state.data?.status === 'running' ? 1000 : false,
  })
}
