import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useRebuildStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['rebuild', jobId],
    queryFn: () => api.getRebuildStatus(jobId as string),
    enabled: jobId !== null,
    // See useOrganizeStatus for why query.state.status is checked too — a
    // stale "running" data value survives a failed refetch, so relying on
    // data alone means a job that 404s (e.g. lost to a server restart)
    // polls forever instead of ever settling into an error.
    refetchInterval: (query) =>
      query.state.status === 'success' && query.state.data?.status === 'running' ? 1000 : false,
  })
}
