![cn-gpu2 chart](chart.png?raw=true "github action runner + gpu dev chart")

## PERMISSIONS:
Have your major advisor email Rob Yelle (robert.yelle@oregonstate.edu) asking to give you permission to the High Performance Computing (HPC) cluster. 

## INTERACTIVE SHEEL WITH GPU:
```
ssh $USER@flip.engr.oregonstate.edu 
ssh $USER@submit-b.hpc.engr.oregonstate.edu 
module load slurm 
# request an interactive bash shell on cn-gpu2 server with gpu(s) 
srun -A eecs -p gpu --nodelist=cn-gpu2 --pty bash --gres=gpu:1 # can be > 1 
```  
## SETUP GITHUB ACTIONS RUNNER:

```
ssh $USER@flip.engr.oregonstate.edu 
ssh $USER@pelican04.eecs.oregonstate.edu 
ssh-keygen -t rsa -b 4096 -C "$USER@pelican03.eecs.oregonstate.edu" 
cat .ssh/id_rsa.pub | ssh $USER@submit-b.hpc.engr.oregonstate.edu 'cat >> .ssh/authorized_keys' 
tmux 
```
Follow the steps of this 2 min [video](https://youtu.be/GHVSRc1BYCc%20Github%20Actions%20Tutorial) to setup a github action runner.
Disconnect the current tmux server by pressing Ctrl+b followed by d. Your runner will stay open after logging out. Nohup can be used to a similar effect: 

```nohup ./run.sh &```

## RESTRICTED GUI WITH GPU:

You can use the GPUs with many applications that require a monitor/GUI. This method does not currently support visualizing unity while it is rendering. A work around is to save debug images & videos to the file system and view them with dolphin & eog: 
 
```
# opens file explorer 
dolphin & 
# view just 1 image
eog selfie.png & 
```
Logging in to use a GUI with the GPUs is very similar to normal:
```
# if -X doesn't work try -Y
ssh -X $USER@flip.engr.oregonstate.edu
ssh -X $USER@submit-b.hpc.engr.oregonstate.edu 
module load slurm 
# request an interactive bash shell on cn-gpu2 server with gpu(s) 
srun -A eecs -p gpu --nodelist=cn-gpu2 --pty bash --gres=gpu:1 --x11
```

## MISC RESOURCES:
Useful slurm (ie sbatch, srun, etc) info: [Link 1](https://it.engineering.oregonstate.edu/hpc/slurm-howto) [Link 2](https://cosine.oregonstate.edu/faqs/unix-hpc-cluster#faq-How-do-I-connect-to-the-cluster)
2 minute github action [tutorial](https://youtu.be/GHVSRc1BYCc%20Github%20Actions%20Tutorial). Useful for setting up your own runner or workflow.


 

 