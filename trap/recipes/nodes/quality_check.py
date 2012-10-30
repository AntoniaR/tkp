import sys
from lofarpipe.support.lofarnode import LOFARnodeTCP
import trap.quality


class quality_check(LOFARnodeTCP):
    def run(self, image_id, parset_file):
        self.outputs['image_id'] = image_id
        self.outputs['pass'] = trap.quality.noise(image_id, parset_file)

if __name__ == "__main__":
    jobid, jobhost, jobport = sys.argv[1:4]
    sys.exit(quality_check(jobid, jobhost, jobport).run_with_stored_arguments())