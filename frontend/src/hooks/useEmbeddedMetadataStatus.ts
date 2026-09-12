import { useQuery } from '@tanstack/react-query'
import { api } from '../services/api'

export function useEmbeddedMetadataStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['embedded-metadata', jobId],
    queryFn: () => api.getEmbeddedMetadataStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      query.state.status === 'success' && query.state.data?.status === 'running' ? 2000 : false,
  })
}
