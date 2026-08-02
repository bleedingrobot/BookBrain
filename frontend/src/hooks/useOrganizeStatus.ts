import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useOrganizeStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['organize', jobId],
    queryFn: () => api.getOrganizeStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1000 : false),
  })
}
